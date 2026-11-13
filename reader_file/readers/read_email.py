"""
Email file readers - Enhanced Version
Aligned with database design principles - all functions within class
- Supports all file sizes and types
- Merges all message content into single message
- Preserves attachment extraction logic
"""

from email import policy
from email.parser import BytesParser
import os
import time
from typing import Dict, Any, Optional, Set
from pathlib import Path

from core.detect_binanry_utils import detect_file_type , get_filename_with_correct_extension
from core.file_utils import sanitize_filename
from core.path_utils import get_extraction_name_file

from .base_reader import BaseReader

from Hdg_Err_Ex_Log import (
    handle_error,
    ErrorCategory,
    ErrorSeverity,
    format_validation_error
)

import logging
logger = logging.getLogger(__name__)


class EmailFileReader(BaseReader):

    @staticmethod
    def _all_headers(msg, name: str, default: str = "") -> str:
        """Collect ALL occurrences of a header, joined with ", ".

        A message has ONE sender but can have one or MORE recipients, and
        the To/Cc/Bcc headers may legally appear multiple times. The stdlib
        ``msg.get(name)`` returns only the first occurrence - silently
        dropping recipients. This joins every occurrence (each may itself
        hold a comma-separated list) so no recipient is ever lost.
        """
        try:
            values = msg.get_all(name)
        except Exception:
            values = None
        if not values:
            return default
        return ", ".join(str(v).strip() for v in values if str(v).strip())
    """
    Reader for email files.
    
    Follows database design principles:
    - All functions are within the class
    - Inherits from BaseReader
    - Consistent error handling
    - Proper resource management
    """
    
    def get_supported_extensions(self) -> Set[str]:
        """Return set of supported email extensions"""
        return {
            '.eml',
            '.msg',
            '.mbox',
            '.pst',
            '.ost',    # Outlook offline store: same container as PST
            '.emlx',   # Apple Mail message (RFC822 plus a plist trailer)
            '.mbx',    # mbox mailbox, alternative spelling
        }
    
    #: Email formats the detector can identify from bytes alone.
    #: .msg is recognised as an OLE2 container holding a __substg1.0_ entry.
    #: The text formats are included because a caller that has already
    #: content-verified them must be believed.
    CONTENT_IDENTIFIABLE = frozenset({'.msg', '.eml', '.mbox', '.emlx', '.mbx'})

    #: What content detection reports when it has no idea. Not an
    #: identification, so it must never trigger a rejection.
    UNIDENTIFIED = frozenset({'.bin', '.ole', ''})

    #: Header lines that reliably open an RFC822 message or an mbox record.
    #: Neither format has magic bytes, so this is the only content signal that
    #: exists for them.
    RFC822_HEADERS = (
        b'from:', b'received:', b'return-path:', b'mime-version:',
        b'date:', b'subject:', b'message-id:', b'to:', b'delivered-to:',
    )

    #: OLE-based email formats with no format-specific signature. The detector
    #: can confirm the container ('.ole') but cannot tell a PST from any other
    #: OLE file, so the declared extension has to settle it.
    OLE_BASED = frozenset({'.pst', '.ost'})

    #: Plain-text email formats. They have no magic bytes at all, so content
    #: detection reports ('.bin', 'none') and the declared extension is the only
    #: available signal.
    TEXT_BASED = frozenset({'.eml', '.mbox', '.emlx', '.mbx'})

    def _resolve_email_type(self, file_info: Dict[str, Any], file_path: str):
        """Return (email_type, rejection_reason) for this file.

        ``rejection_reason`` is a non-empty string when the content positively
        identifies something that is not an email container at all - in which
        case the declared extension must not be trusted, because handing a ZIP
        to extract_msg produces a confusing parse error rather than the truth.
        """
        # Read the router's content-verified value directly rather than via
        # self.effective_extension(), which falls back to the path's own suffix
        # when detection produced nothing. Conflating the two made the content
        # rule fire on the declared extension, so an .eml renamed to .msg was
        # treated as content-verified MSG.
        raw_detected = str((file_info or {}).get('effective_extension') or '').lower()
        detected = raw_detected if raw_detected.startswith('.') else (
            '.' + raw_detected if raw_detected else ''
        )
        declared = os.path.splitext(file_path)[1].lower()

        # 1. Content wins outright when it names an email format.
        if detected in self.CONTENT_IDENTIFIABLE:
            return detected, None

        # 2. An OLE container whose declared extension is a known OLE-based email
        #    format. There is no stronger signature available for PST.
        if detected == '.ole' and declared in self.OLE_BASED:
            return declared, None

        # 3. Content positively identifies something that is not an email
        #    container. Reject rather than mis-parse: handing a ZIP to
        #    extract_msg yields a confusing parse error instead of the truth.
        #    '.bin'/'.ole' are excluded because they mean unidentified, not
        #    identified-as-something-else.
        if detected not in self.UNIDENTIFIED and detected != declared:
            return declared, (
                f"Content is {detected}, not an email container: {file_path}"
            )

        # 4. Plain-text formats have no magic bytes, so the declared extension
        #    is the only signal the detector can offer - but an extension is a
        #    claim, not evidence. A file named .eml whose bytes carry no RFC822
        #    header field at all is not a message, and reporting it as an
        #    extracted email would turn garbage into a silent success.
        # 4. Plain-text formats have no magic bytes, so the declared extension
        #    is the only signal the detector can offer. Whether the bytes
        #    actually look like a message is decided in read_file, where the
        #    answer is reported as an unsupported email type rather than as a
        #    rejection (see _has_rfc822_headers).
        if declared in self.TEXT_BASED:
            return declared, None

        # 5. No usable extension. Sniff the bytes: an RFC822 message or mbox
        #    record under a wrong or missing extension is still an email, and
        #    discarding it would drop real content.
        sniffed = self._sniff_text_email(file_path)
        if sniffed:
            return sniffed, None

        # 6. Fall back to the declared extension; the dispatcher rejects it if
        #    it is not an email format at all.
        return declared, None

    def _has_rfc822_headers(self, file_path: str) -> bool:
        """True when the file opens like an RFC822 message or an mbox record.

        A bounded read of the first kilobyte; the check is deliberately about
        *evidence* (at least one header field or an mbox envelope) rather than
        about the extension, so a renamed message is still processed and a
        mislabelled binary is still refused.
        """
        try:
            with open(file_path, 'rb') as handle:
                head = handle.read(1024)
        except OSError:
            return False
        if not head:
            return False
        if head.startswith(b'From '):
            return True
        lowered = head[:4096].lower()
        return any(marker in lowered for marker in self.RFC822_HEADERS)

    def _sniff_text_email(self, file_path: str):
        """Return '.mbox', '.eml' or None based on the leading bytes.

        Bounded read: only the first kilobyte is examined, so a large file is
        never fully loaded to answer this question.
        """
        try:
            with open(file_path, 'rb') as handle:
                head = handle.read(1024)
        except OSError:
            return None
        if not head:
            return None
        # An mbox record begins with a "From " envelope line, which is distinct
        # from the "From:" header of an RFC822 message.
        if head.startswith(b'From '):
            return '.mbox'
        first_line = head.split(b'\n', 1)[0].strip().lower()
        if first_line.startswith(self.RFC822_HEADERS) and b':' in first_line:
            return '.eml'
        return None

    def read_file(self, file_info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Read email file and extract content with improved error handling
        
        Args:
            file_info: Dictionary containing file information with 'path' key
        
        Returns:
            Dictionary with email content or error information
        """
        # Validate file info using base class method
        is_valid, error_msg = self.validate_file_info(file_info)
        if not is_valid:
            return self.create_error_result(error_msg or "Invalid file info", file_info.get("path", "unknown"))
        
        file_path = str(file_info.get("path"))

        # DETECT-01 for email: dispatch on the content-verified type, not the
        # filename. Previously this used file_lower.endswith(...), so a message
        # renamed to .txt never reached this reader and a non-message renamed to
        # .msg was handed to extract_msg. Measured with
        # detect_file_type_with_confidence, the three signals differ per format:
        #
        #     .msg   -> ('.msg', 'strong')  OLE2 + the __substg1.0_ entry
        #     .pst   -> ('.ole', 'strong')  OLE2, but no PST-specific signature
        #     .eml   -> ('.bin', 'none')    plain text, no magic bytes
        #     .mbox  -> ('.bin', 'none')    plain text, no magic bytes
        #
        # So no single rule covers all four: content wins where content exists,
        # and the declared extension is the only signal for the text formats.
        email_type, rejection = self._resolve_email_type(file_info, file_path)
        if rejection:
            return self.handle_read_error(
                ValueError(rejection), file_path, "read_file"
            )

        # A text-based email format is named by its extension alone, so the
        # bytes have to be checked before a parser is pointed at them: a file
        # named .eml whose content carries no RFC822 header field and no mbox
        # envelope is not a message, and reporting it as extracted email would
        # turn garbage into a silent success. The outcome is 'unsupported' (the
        # container was named, the content does not match) rather than 'failed',
        # which is the distinction the ledger records.
        if email_type in self.TEXT_BASED and not self._has_rfc822_headers(file_path):
            return self.handle_read_error(
                ValueError(
                    f"Unsupported email type: {file_path} has a text-email "
                    f"extension ({email_type}) but no RFC822 header fields and "
                    f"no mbox envelope, so it is not an email container"
                ),
                file_path, "read_file",
            )

        try:
            result = None

            if email_type == '.msg':
                result = self.extract_msg(file_path)
                if result and 'error' not in result:
                    result["email_type"] = "msg"

            elif email_type == '.eml':
                result = self.extract_eml(file_path)
                if result and 'error' not in result:
                    result["email_type"] = "eml"

            elif email_type in ('.mbox', '.mbx'):
                result = self.extract_mbox(file_path)
                if result and 'error' not in result:
                    result["email_type"] = "mbox"

            elif email_type == '.emlx':
                result = self.extract_emlx(file_path)
                if result and 'error' not in result:
                    result["email_type"] = "emlx"

            elif email_type in ('.pst', '.ost'):
                # PST and OST are the same container family (both parsed by
                # libpff); the reader records which one was declared rather than
                # pretending the two are interchangeable.
                result = self.extract_pst_pypff(file_path)
                if result and 'error' not in result:
                    result["email_type"] = email_type.lstrip('.')
            else:
                error_msg = f"Unsupported email type: {file_path}"
                return self.handle_read_error(ValueError(error_msg), file_path, "read_file")
            
            # If extraction failed, ensure we have an error result
            if not result or 'error' in result:
                if not result:
                    result = {"error": "Email extraction failed", "path": file_path}
            
            return result
            
        except Exception as e:
            return self.handle_read_error(e, file_path, "read_file")
    
    def extract_emlx(self, filepath):
        """Extract an Apple Mail ``.emlx`` message.

        The format is an RFC822 message with two Apple additions: a leading
        byte-count line, and a plist trailer holding Mail's own flags
        (``flags``, ``date-received``, ``remote-id``). Both are evidence, so
        both are recorded; the message itself is handed to the ordinary EML
        path rather than re-implemented here, which keeps one MIME parser in
        the pipeline.

        A file whose byte count is missing or inconsistent is still processed -
        the declared count is recorded as a discrepancy instead of causing the
        message to be dropped.
        """
        try:
            if not os.path.exists(filepath):
                return {"error": "File not found", "filepath": filepath}

            with open(filepath, "rb") as handle:
                raw = handle.read()

            notes = {"emlx": True}
            declared_length = None
            body_start = 0
            first_newline = raw.find(b"\n")
            if 0 < first_newline < 20:
                header = raw[:first_newline].strip()
                if header.isdigit():
                    declared_length = int(header)
                    body_start = first_newline + 1
            if declared_length is None:
                notes["length_line"] = "absent or not numeric; message parsed from the first line"
                message_bytes = raw
            else:
                message_bytes = raw[body_start: body_start + declared_length]
                if len(message_bytes) != declared_length:
                    notes["length_line"] = (
                        f"header declares {declared_length} bytes but only "
                        f"{len(message_bytes)} are present"
                    )
                notes["declared_length"] = declared_length

            trailer = raw[body_start + (declared_length or 0):] if declared_length else b""
            if trailer.strip():
                try:
                    import plistlib

                    plist = plistlib.loads(trailer[trailer.find(b"<?xml"):])
                    notes["mail_flags"] = {
                        key: value for key, value in (plist or {}).items()
                        if key in ("flags", "date-received", "remote-id", "subject",
                                   "sender", "original-mailbox", "message-id")
                    }
                except Exception as exc:
                    notes["plist_error"] = str(exc)

            # Write the message body to the artifact's extraction directory and
            # let the established EML parser handle MIME structure, bodies and
            # attachments - one parser, one set of behaviours to maintain.
            extract_to = get_extraction_name_file(filepath, ".emlx")
            os.makedirs(extract_to, exist_ok=True)
            materialised = os.path.join(extract_to, os.path.basename(filepath) + ".eml")
            with open(materialised, "wb") as handle:
                handle.write(message_bytes)

            result = self.extract_eml(materialised)
            if isinstance(result, dict):
                result["filepath"] = filepath
                result["source_filepath"] = filepath
                result["emlx"] = notes
            return result
        except Exception as e:
            return self.handle_read_error(e, filepath, "extract_emlx")

    def extract_eml(self, filepath):
        """
        Extract EML file with complete separation:
        - Message content returned directly with metadata (no file created)
        - ALL content parts merged into single message
        - Attachments saved as separate physical files for independent processing
        Returns dict with message data and extraction path for attachments
        """
        try:
            if not os.path.exists(filepath):
                return {"error": "File not found", "filepath": filepath}

            with open(filepath, 'rb') as f:
                msg = BytesParser(policy=policy.default).parse(f)

            # Create extraction folder for attachments only
            extract_to = get_extraction_name_file(filepath, '.eml')
            os.makedirs(extract_to, exist_ok=True)

            # -----------------------------------------
            # 1. EXTRACT MESSAGE METADATA + ALL BODY CONTENT (MERGED)
            # -----------------------------------------
            message = {
                "source_filepath": filepath,
                "from": self._all_headers(msg, 'From'),
                "to": self._all_headers(msg, 'To'),
                "subject": self._all_headers(msg, 'Subject'),
                "date": self._all_headers(msg, 'Date'),
                "cc": self._all_headers(msg, 'Cc'),
                "bcc": self._all_headers(msg, 'Bcc'),
                "message_id": self._all_headers(msg, 'Message-ID'),
                "content": ""  # Single merged content string
            }

            # Collect all text content parts
            content_parts = []
            
            # Import resource coordinator for CPU management
            try:
                from core.resource_coordinator import should_yield, get_yield_duration
                resource_monitoring_available = True
            except ImportError:
                resource_monitoring_available = False
                should_yield = lambda *args: False
                get_yield_duration = lambda: 0.0
            
            for part in msg.walk():
                content_type = part.get_content_type()
                
                # Only process text content (not attachments)
                if content_type in ['text/plain', 'text/html']:
                    disposition = part.get_content_disposition()
                    # Skip if it's an attachment
                    if disposition not in ['attachment', 'inline']:
                        try:
                            raw_content = part.get_payload(decode=True)
                            if raw_content:
                                text_content = raw_content.decode('utf-8', errors='ignore')
                                
                                # Convert HTML to plaintext if needed
                                if content_type == "text/html":
                                    try:
                                        import html2text
                                        text_content = html2text.html2text(text_content)
                                    except ImportError:
                                        import re
                                        text_content = re.sub('<[^<]+?>', '', text_content)
                                
                                if text_content.strip():
                                    content_parts.append(text_content.strip())
                                    
                                    # Yield periodically during content extraction to prevent CPU spikes
                                    if resource_monitoring_available and len(content_parts) % 10 == 0:
                                        if should_yield():
                                            time.sleep(get_yield_duration())
                        except Exception as e:
                            print(f"  ⚠ Could not decode text part: {str(e)}")
                            continue

            # Merge all content parts with double newline separator
            message["content"] = "\n\n".join(content_parts) if content_parts else ""

            # -----------------------------------------
            # 2. PROCESS ATTACHMENTS
            # - HTML attachments: Extract content directly and add to message
            # - Other attachments: Save as separate files
            # -----------------------------------------
            attachment_count = 0
            html_attachments_content = []  # Store HTML attachment content

            for part in msg.walk():
                disposition = part.get_content_disposition()

                if disposition in ['attachment', 'inline']:
                    original_filename = part.get_filename() or "unnamed"
                    payload = part.get_payload(decode=True)
                    
                    if not payload:
                        continue
                    
                    # Detect file type
                    filename = sanitize_filename(original_filename)
                    filename = get_filename_with_correct_extension(filename, payload)
                    detected_type = detect_file_type(payload)
                    
                    # Check if attachment is HTML
                    content_type = part.get_content_type()
                    is_html = (filename.lower().endswith(('.html', '.htm')) or 
                              detected_type == 'HTML' or
                              'html' in content_type.lower())
                    
                    if is_html:
                        # Extract HTML content directly and add to message
                        try:
                            html_content = payload.decode('utf-8', errors='ignore')
                            if html_content.strip():
                                # Try to convert HTML to text for readability
                                try:
                                    import html2text
                                    html_text = html2text.html2text(html_content)
                                    html_attachments_content.append(f"\n\n--- HTML Attachment: {original_filename} ---\n{html_text.strip()}")
                                except Exception:
                                    # If html2text fails, add raw HTML (limited)
                                    html_attachments_content.append(f"\n\n--- HTML Attachment: {original_filename} ---\n{html_content[:5000]}")
                                print(f"  ✓ Extracted HTML attachment: {original_filename} (content added to message)")
                                attachment_count += 1
                        except Exception as e:
                            print(f"  ⚠ Warning: Failed to extract HTML from {original_filename}: {str(e)}")
                            # Fall through to save as file
                            is_html = False
                    
                    if not is_html:
                        # Save non-HTML attachments as separate files
                        # Save attachment as physical file
                        filepath_attach = os.path.join(extract_to, filename)
                        
                        # Handle duplicate filenames
                        counter = 1
                        base_name = Path(filename).stem
                        extension = Path(filename).suffix
                        while os.path.exists(filepath_attach):
                            filename = f"{base_name}_{counter}{extension}"
                            filepath_attach = os.path.join(extract_to, filename)
                            counter += 1

                        # Write attachment to disk
                        try:
                            with open(filepath_attach, 'wb') as f:
                                f.write(payload)
                                f.flush()  # Ensure data is written to OS buffer
                                os.fsync(f.fileno())  # Force write to disk
                            
                            # Verify file was written correctly
                            if not os.path.exists(filepath_attach):
                                print(f"  ⚠ Warning: File {filename} was not found after writing")
                                continue
                            if os.path.getsize(filepath_attach) != len(payload):
                                print(f"  ⚠ Warning: File {filename} size mismatch (expected {len(payload)}, got {os.path.getsize(filepath_attach)})")
                                continue
                            
                            attachment_count += 1
                            detected_type = detect_file_type(payload)
                        except Exception as e:
                            print(f"  ✗ Error writing attachment {filename}: {str(e)}")
                            continue
                        if original_filename != filename:
                            print(f"  ✓ Saved attachment: {filename} ({len(payload)} bytes) [detected: {detected_type}]")
                        else:
                            print(f"  ✓ Saved attachment: {filename} ({len(payload)} bytes)")
                        
                        # Yield to prevent CPU overload during batch attachment processing
                        if resource_monitoring_available:
                            if should_yield(aggressive=True):
                                yield_duration = get_yield_duration()
                                if yield_duration > 0:
                                    time.sleep(yield_duration)

            # Add HTML attachments content to message
            if html_attachments_content:
                if message.get("content"):
                    message["content"] += "\n".join(html_attachments_content)
                else:
                    message["content"] = "\n".join(html_attachments_content)

            # -----------------------------------------
            # RETURN MESSAGE DATA + EXTRACTION PATH
            # -----------------------------------------
            if attachment_count > 0:
                print(f"✓ Extracted {attachment_count} attachment(s) from {filepath}")
            else:
                print(f"✓ No attachments found in {filepath}")
            
            if attachment_count > 0:
                print(f"✓ Attachments saved to: {extract_to}/")
            
            return {
                "message": message,                    # Message content with metadata (merged content)
                "extraction_path": extract_to,         # Path to attachments folder
                "attachment_count": attachment_count,
                "has_attachments": attachment_count > 0
            }

        except Exception as e:
            print(f"✗ Error extracting {filepath}: {str(e)}")
            return {"error": str(e), "filepath": filepath}
    
    def extract_msg(self, file_path):
        """
        Extract MSG file with complete separation:
        - Message metadata returned directly with merged content
        - Attachments saved as separate physical files for processing
        """
        try:
            import extract_msg
        except ImportError:
            print("⚠ extract-msg not installed. Install with: pip install extract-msg")
            return {"error": "extract-msg package not installed"}
        
        extract_to = get_extraction_name_file(file_path, '.msg')
        os.makedirs(extract_to, exist_ok=True)
        
        try:
            msg = extract_msg.Message(file_path)
            

            content_parts = []
            
            # Add main body
            if msg.body:
                content_parts.append(msg.body.strip())
            
            # Add HTML body if different (converted to text)
            if hasattr(msg, 'htmlBody') and msg.htmlBody:
                try:
                    import html2text
                    html_text = html2text.html2text(msg.htmlBody)
                    if html_text.strip() and html_text.strip() != content_parts[0] if content_parts else True:
                        content_parts.append(html_text.strip())
                except Exception:
                    pass
            
            # One sender, one or MORE recipients: collect every recipient
            # (the recipients property is complete; the To header string is
            # the fallback) and every CC/BCC entry.
            all_recipients = []
            try:
                for recipient in (msg.recipients or []):
                    addr = getattr(recipient, 'email', None) or getattr(recipient, 'name', None)
                    if addr and addr not in all_recipients:
                        all_recipients.append(addr)
            except Exception:
                all_recipients = []
            to_value = ", ".join(all_recipients) if all_recipients else (msg.to or '')

            message = {
                "source_filepath": file_path,
                "from": msg.sender or '',
                "to": to_value,
                "subject": msg.subject or '',
                "date": str(msg.date or ''),
                "cc": msg.cc or '',
                "bcc": msg.bcc or '',
                "content": "\n\n".join(content_parts) if content_parts else ""
            }
            
            # -----------------------------------------
            # 2. PROCESS ATTACHMENTS
            # - HTML attachments: Extract content directly and add to message
            # - Other attachments: Save as separate files
            # -----------------------------------------
            attachment_count = 0
            html_attachments_content = []  # Store HTML attachment content
            
            # Import resource coordinator for CPU management (for PST/OST files)
            try:
                from core.resource_coordinator import should_yield, get_yield_duration
                resource_monitoring_available_pst = True
            except ImportError:
                resource_monitoring_available_pst = False
                def should_yield(*args):
                    return False
                def get_yield_duration():
                    return 0.0
            
            for attachment in msg.attachments:
                original_filename = attachment.longFilename or attachment.shortFilename or "unnamed"
                filename = sanitize_filename(original_filename)
                
                # Detect correct file type and update filename
                filename = get_filename_with_correct_extension(filename, attachment.data)
                detected_type = detect_file_type(attachment.data)
                
                # Check if attachment is HTML
                is_html = (filename.lower().endswith(('.html', '.htm')) or 
                          detected_type == 'HTML' or
                          (hasattr(attachment, 'mimeType') and 'html' in attachment.mimeType.lower()))
                
                if is_html:
                    # Extract HTML content directly and add to message
                    try:
                        html_content = attachment.data.decode('utf-8', errors='ignore')
                        if html_content.strip():
                            # Try to convert HTML to text for readability
                            try:
                                import html2text
                                html_text = html2text.html2text(html_content)
                                html_attachments_content.append(f"\n\n--- HTML Attachment: {original_filename} ---\n{html_text.strip()}")
                            except Exception:
                                # If html2text fails, add raw HTML
                                html_attachments_content.append(f"\n\n--- HTML Attachment: {original_filename} ---\n{html_content[:5000]}")  # Limit to 5000 chars
                            print(f"  ✓ Extracted HTML attachment: {original_filename} (content added to message)")
                            attachment_count += 1
                    except Exception as e:
                        print(f"  ⚠ Warning: Failed to extract HTML from {original_filename}: {str(e)}")
                        # Fall through to save as file
                        is_html = False
                
                if not is_html:
                    # Save non-HTML attachments as separate files
                    filepath_attach = os.path.join(extract_to, filename)
                    
                    counter = 1
                    base_name = Path(filename).stem
                    extension = Path(filename).suffix
                    while os.path.exists(filepath_attach):
                        filename = f"{base_name}_{counter}{extension}"
                        filepath_attach = os.path.join(extract_to, filename)
                        counter += 1
                    
                    try:
                        with open(filepath_attach, 'wb') as f:
                            f.write(attachment.data)
                            f.flush()  # Ensure data is written to OS buffer
                            os.fsync(f.fileno())  # Force write to disk
                        
                        # Verify file was written correctly
                        if not os.path.exists(filepath_attach):
                            print(f"  ⚠ Warning: File {filename} was not found after writing")
                            continue
                        if os.path.getsize(filepath_attach) != len(attachment.data):
                            print(f"  ⚠ Warning: File {filename} size mismatch")
                            continue
                        
                        attachment_count += 1
                    except Exception as e:
                        print(f"  ✗ Error writing attachment {filename}: {str(e)}")
                        continue
                    
                    if original_filename != filename:
                        print(f"  ✓ Saved attachment: {filename} [detected: {detected_type}]")
                    else:
                        print(f"  ✓ Saved attachment: {filename}")
                    
                    # Yield to prevent CPU overload during batch attachment processing
                    if resource_monitoring_available_pst:
                        if should_yield(aggressive=True):
                            yield_duration = get_yield_duration()
                            if yield_duration > 0:
                                time.sleep(yield_duration)
            
            # Add HTML attachments content to message
            if html_attachments_content:
                if message.get("content"):
                    message["content"] += "\n".join(html_attachments_content)
                else:
                    message["content"] = "\n".join(html_attachments_content)
            
            try:
                msg.close()
            except:
                pass
            
            if attachment_count > 0:
                print(f"✓ Extracted {attachment_count} attachment(s) from {file_path}")
                print(f"✓ Attachments saved to: {extract_to}/")
            else:
                print(f"✓ No attachments found in {file_path}")
            
            return {
                "message": message,
                "extraction_path": extract_to,
                "attachment_count": attachment_count,
                "has_attachments": attachment_count > 0
            }
            
        except Exception as e:
            print(f"✗ Error extracting {file_path}: {str(e)}")
            return {"error": str(e), "filepath": file_path}
    
    def extract_mbox(self, file_path):
        """
        Extract MBOX file with complete separation:
        - Each message's metadata returned with merged content
        - Attachments saved as separate physical files in subfolders
        """
        import mailbox
        
        extract_to = get_extraction_name_file(file_path, '.mbox')
        os.makedirs(extract_to, exist_ok=True)
        
        total_attachments = 0
        messages_data = []
        
        try:
            mbox = mailbox.mbox(file_path)
            
            for idx, message in enumerate(mbox):
                subject = message.get('Subject', 'No Subject')
                safe_subject = sanitize_filename(subject)[:50]
                
                message_folder = os.path.join(extract_to, f"msg_{idx:03d}_{safe_subject}")
                
                # -----------------------------------------
                # 1. EXTRACT MESSAGE METADATA + MERGED BODY CONTENT
                # -----------------------------------------
                content_parts = []
                
                # Extract all text content
                for part in message.walk():
                    if part.get_content_type() in ['text/plain', 'text/html']:
                        disposition = part.get_content_disposition()
                        # Skip attachments
                        if disposition not in ['attachment', 'inline']:
                            try:
                                content = part.get_payload(decode=True)
                                if content:
                                    text_content = content.decode('utf-8', errors='ignore')
                                    
                                    # Convert HTML to plain text
                                    if part.get_content_type() == 'text/html':
                                        try:
                                            import html2text
                                            text_content = html2text.html2text(text_content)
                                        except ImportError:
                                            import re
                                            text_content = re.sub('<[^<]+?>', '', text_content)
                                    
                                    if text_content.strip():
                                        content_parts.append(text_content.strip())
                            except Exception:
                                continue
                
                msg_data = {
                    "source_filepath": file_path,
                    "message_index": idx,
                    "from": self._all_headers(message, 'From'),
                    "to": self._all_headers(message, 'To'),
                    "subject": subject,
                    "date": self._all_headers(message, 'Date'),
                    "cc": self._all_headers(message, 'Cc'),
                    "message_id": self._all_headers(message, 'Message-ID'),
                    "content": "\n\n".join(content_parts) if content_parts else ""
                }
                
                # -----------------------------------------
                # 2. PROCESS ATTACHMENTS
                # - HTML attachments: Extract content directly and add to message
                # - Other attachments: Save as separate files
                # -----------------------------------------
                message_attachment_count = 0
                html_attachments_content = []  # Store HTML attachment content for this message
                
                for part in message.walk():
                    if part.get_content_disposition() in ['attachment', 'inline']:
                        original_filename = part.get_filename()
                        if original_filename:
                            payload = part.get_payload(decode=True)
                            if not payload:
                                continue
                            
                            filename = sanitize_filename(original_filename)
                            # Detect correct file type and update filename
                            filename = get_filename_with_correct_extension(filename, payload)
                            detected_type = detect_file_type(payload)
                            
                            # Check if attachment is HTML
                            content_type = part.get_content_type()
                            is_html = (filename.lower().endswith(('.html', '.htm')) or 
                                      detected_type == 'HTML' or
                                      'html' in content_type.lower())
                            
                            if is_html:
                                # Extract HTML content directly and add to message
                                try:
                                    html_content = payload.decode('utf-8', errors='ignore')
                                    if html_content.strip():
                                        # Try to convert HTML to text for readability
                                        try:
                                            import html2text
                                            html_text = html2text.html2text(html_content)
                                            html_attachments_content.append(f"\n\n--- HTML Attachment: {original_filename} ---\n{html_text.strip()}")
                                        except Exception:
                                            # If html2text fails, add raw HTML (limited)
                                            html_attachments_content.append(f"\n\n--- HTML Attachment: {original_filename} ---\n{html_content[:5000]}")
                                        message_attachment_count += 1
                                        total_attachments += 1
                                except Exception:
                                    # Fall through to save as file
                                    is_html = False
                            
                            if not is_html:
                                # Save non-HTML attachments as separate files
                                # Create message folder only if it has attachments
                                if message_attachment_count == 0:
                                    os.makedirs(message_folder, exist_ok=True)
                                
                                filepath_attach = os.path.join(message_folder, filename)
                                
                                counter = 1
                                base_name = Path(filename).stem
                                extension = Path(filename).suffix
                                while os.path.exists(filepath_attach):
                                    filename = f"{base_name}_{counter}{extension}"
                                    filepath_attach = os.path.join(message_folder, filename)
                                    counter += 1
                                
                                try:
                                    with open(filepath_attach, 'wb') as f:
                                        f.write(payload)
                                        f.flush()  # Ensure data is written to OS buffer
                                        os.fsync(f.fileno())  # Force write to disk
                                    
                                    # Verify file was written correctly
                                    if not os.path.exists(filepath_attach):
                                        continue
                                    if os.path.getsize(filepath_attach) != len(payload):
                                        continue
                                    
                                    message_attachment_count += 1
                                    total_attachments += 1
                                except Exception:
                                    continue
                
                # Add HTML attachments content to message
                if html_attachments_content:
                    if msg_data.get("content"):
                        msg_data["content"] += "\n".join(html_attachments_content)
                    else:
                        msg_data["content"] = "\n".join(html_attachments_content)
                
                # Add attachment info to message data
                msg_data["attachment_count"] = message_attachment_count
                msg_data["has_attachments"] = message_attachment_count > 0
                if message_attachment_count > 0:
                    msg_data["attachments_folder"] = message_folder
                
                messages_data.append(msg_data)
            
            if total_attachments > 0:
                print(f"✓ Extracted {total_attachments} attachment(s) from {file_path}")
                print(f"✓ Attachments saved to: {extract_to}/")
            else:
                print(f"✓ No attachments found in {file_path}")
            
            return {
                "messages": messages_data,
                "extraction_path": extract_to,
                "total_messages": len(messages_data),
                "total_attachments": total_attachments,
                # EMAIL-02: the router decides whether to process attachments
                # from `has_attachments`, and counts them from
                # `attachment_count`. extract_eml returns both; extract_mbox
                # returned neither, so the router saw has_attachments=False and
                # skipped the whole attachment branch. Attachments were written
                # to disk and reported in total_attachments, then never stored,
                # indexed or linked to their message - a silent loss with no
                # error anywhere. Emit the same contract as extract_eml.
                "has_attachments": total_attachments > 0,
                "attachment_count": total_attachments,
            }
            
        except Exception as e:
            print(f"✗ Error extracting {file_path}: {str(e)}")
            return {"error": str(e), "filepath": file_path}
    

    def extract_pst_pypff(self, file_path):
        """
        Extract PST file with message content extraction:
        - Message metadata and merged content extracted
        - Attachments saved as separate physical files for processing
        - Fixed to prevent attachment loss
        """
        try:
            import pypff
        except ImportError:
            print("✗ pypff library not installed. Install with: pip install libpff-python")
            return {"error": "pypff package not installed"}
        
        extract_to = get_extraction_name_file(file_path, '.pst')
        os.makedirs(extract_to, exist_ok=True)
        
        total_attachments = 0
        attachment_counter = 0
        messages_data = []
        message_counter = 0
        no_content_messages = 0
        
        try:
            pst = pypff.file()
            pst.open(file_path)
            root = pst.get_root_folder()

            # Optional: RTF conversion
            try:
                from striprtf.striprtf import rtf_to_text  # noqa: F401
                STRIP_RTF_AVAILABLE = True
            except ImportError:
                STRIP_RTF_AVAILABLE = False
                print("⚠ striprtf not installed. RTF bodies will be skipped.")

            import html2text
            h2t = html2text.HTML2Text()
            h2t.ignore_links = True





            def process_folder(folder):
                nonlocal total_attachments, attachment_counter, message_counter, no_content_messages
                
                # Process messages
                for i in range(folder.get_number_of_sub_messages()):
                    if message_counter >= 50000000:
                        return

                    try:
                        message = folder.get_sub_message(i)
                    except Exception as e:
                        print(f"⚠ Cannot access message {i}: {e}")
                        continue

                    message_counter += 1

                    # --- Metadata extraction ---
                    try:
                        from_name = message.get_sender_name() or message.get_sender_email_address() or ""
                    except Exception:
                        from_name = ""
                    try:
                        to_name = message.get_transport_headers() or message.get_recipient_name() or ""
                    except Exception:
                        to_name = ""
                    try:
                        subject = message.get_subject() or ""
                    except Exception:
                        subject = ""
                    try:
                        date = str(message.get_delivery_time() or "")
                    except Exception:
                        date = ""

                    msg_data = {
                        "source_filepath": file_path,
                        "message_index": message_counter,
                        "from": from_name,
                        "to": to_name,
                        "subject": subject,
                        "date": date,
                        "content": "",
                        "attachment_count": 0,
                        "has_attachments": False
                    }

                    recipients = []
                    try:
                        for i in range(message.get_number_of_recipients()):
                            recipient = message.get_recipient(i)
                            if recipient:
                                name = recipient.get_name() or recipient.get_email_address() or ""
                                recipients.append(name)
                    except Exception:
                        pass
                    # --- Body extraction ---
                    content_parts = []

                    # Plain text
                    try:
                        body = message.get_plain_text_body()
                        if isinstance(body, bytes):
                            body = body.decode(errors="replace")
                        if body and body.strip():
                            content_parts.append(body.strip())
                    except Exception as e:
                        print(f"⚠ Plain text error (msg {message_counter}): {e}")

                    # HTML
                    try:
                        body = message.get_html_body()
                        if isinstance(body, bytes):
                            body = body.decode(errors="replace")
                        if body and body.strip():
                            text = h2t.handle(body).strip()
                            if text and (not content_parts or text != content_parts[0]):
                                content_parts.append(text)
                    except Exception as e:
                        # libpff raises this for messages whose HTML property is
                        # absent. It is a normal malformed/HTML-less message,
                        # not a PST-wide extraction failure; keep diagnostics
                        # quiet while plain text and RTF remain available.
                        if "missing value data" in str(e).lower():
                            print(f"  ℹ No HTML body (msg {message_counter})")
                        else:
                            print(f"⚠ HTML error (msg {message_counter}): {e}")

                    # RTF
                    if STRIP_RTF_AVAILABLE:
                        try:
                            body = message.get_rtf_body()
                            # Check if body exists before decoding
                            if body is not None:
                                # Decode safely to avoid charmap/encoding issues
                                body = self._decode_rtf_body(body)
                                if body and body.strip():
                                    text = self._safe_rtf_to_text(body)
                                    if text:
                                        content_parts.append(text)
                        except Exception as e:
                            print(f"⚠ RTF error (msg {message_counter}): {e}")

                    # Combine all content parts
                    msg_data["content"] = "\n\n".join(content_parts) if content_parts else ""
                    
                    # Log warning if no content was extracted (helps diagnose issues)
                    if not msg_data["content"]:
                        no_content_messages += 1
                        # Large PSTs can contain thousands of calendar/system
                        # records without bodies. Keep the useful diagnostic,
                        # but do not flood stdout or the web process log.
                        if no_content_messages <= 25:
                            print(f"  ⚠ Message {message_counter} has no extractable content (subject: {subject[:500] if subject else 'N/A'})")
                    
                    msg_data["to"] = ", ".join(recipients)


                    message_attachment_count = 0
                    
                    try:
                        num_attachments = message.get_number_of_attachments()
                    except Exception:
                        num_attachments = 0
                    
                    if num_attachments > 0:
                        for j in range(num_attachments):
                            try:
                                attachment = message.get_attachment(j)
                                attachment_counter += 1
                                
                                data = None
                                try:
                                    data = attachment.read_buffer(attachment.get_size())
                                except Exception:
                                    try:
                                        data = attachment.read()
                                    except Exception:
                                        try:
                                            data = attachment.data
                                        except Exception:
                                            print(f"  ⚠ Could not read data for attachment {attachment_counter}")
                                            continue
                                
                                if not data:
                                    continue
                                
                                # Get original filename before processing
                                original_filename = None
                                try:
                                    original_filename = attachment.get_name()
                                except Exception:
                                    pass
                                
                                if not original_filename:
                                    extension = detect_file_type(data)
                                    filename = f"attachment_{attachment_counter:05d}{extension}"
                                else:
                                    filename = sanitize_filename(original_filename)
                                    # Always detect and correct the extension based on actual file content
                                    filename = get_filename_with_correct_extension(filename, data)
                                
                                filepath = os.path.join(extract_to, filename)
                                
                                counter = 1
                                base_name = Path(filename).stem
                                extension = Path(filename).suffix
                                while os.path.exists(filepath):
                                    filename = f"{base_name}_{counter}{extension}"
                                    filepath = os.path.join(extract_to, filename)
                                    counter += 1
                                
                                try:
                                    with open(filepath, 'wb') as f:
                                        f.write(data)
                                        f.flush()  # Ensure data is written to OS buffer
                                        os.fsync(f.fileno())  # Force write to disk
                                    
                                    # Verify file was written correctly
                                    if not os.path.exists(filepath):
                                        print(f"  ⚠ Warning: File {filename} was not found after writing")
                                        continue
                                    if os.path.getsize(filepath) != len(data):
                                        print(f"  ⚠ Warning: File {filename} size mismatch")
                                        continue
                                    
                                    total_attachments += 1
                                    message_attachment_count += 1
                                except Exception as e:
                                    print(f"  ⚠ Could not write attachment {filename}: {str(e)[:80]}")
                                    continue
                                detected_type = detect_file_type(data)
                                # Show detection info if filename was changed
                                if original_filename and filename != original_filename:
                                    print(f"  Extracted: {filename} ({len(data)} bytes) [detected: {detected_type}, was: {Path(original_filename).suffix or 'no ext'}]")
                                else:
                                    print(f"  Extracted: {filename} ({len(data)} bytes)")
                                
                            except Exception as e:
                                print(f"  ⚠ Could not extract attachment {attachment_counter}: {str(e)[:80]}")
                                continue
                    
                    # Add attachment info to message data
                    msg_data["attachment_count"] = message_attachment_count
                    msg_data["has_attachments"] = message_attachment_count > 0
                    
                    messages_data.append(msg_data)
                
                # Process subfolders
                for i in range(folder.get_number_of_sub_folders()):
                    try:
                        subfolder = folder.get_sub_folder(i)
                        process_folder(subfolder)
                    except Exception:
                        continue
            
            print(f"Processing PST file: {file_path}")
            print(f"Extracting to: {extract_to}/")
            print()
            process_folder(root)
            pst.close()
            
            print()
            if total_attachments > 0:
                print(f"✓ Successfully extracted {total_attachments} attachment(s)")
                print(f"✓ Saved to: {extract_to}/")
            else:
                print(f"✓ No attachments found in {file_path}")
            
            print(f"✓ Processed {message_counter} message(s)")
            if no_content_messages > 25:
                print(f"ℹ Suppressed {no_content_messages - 25} additional no-content message warnings")
            
            return {
                "messages": messages_data,
                "extraction_path": extract_to,
                "total_messages": message_counter,
                "attachment_count": total_attachments,
                "has_attachments": total_attachments > 0
            }
        
        except Exception as e:
            print(f"✗ pypff Error: {str(e)[:200]}")
            return {"error": str(e), "filepath": file_path}
        
        
    
    def _decode_rtf_body(self, raw_body):
        """Decode RTF body bytes using several encodings with fallbacks."""
        if raw_body is None:
            return None
        if isinstance(raw_body, str):
            return raw_body
        if not isinstance(raw_body, bytes):
            return None
        for enc in ("utf-8", "cp1252", "latin-1"):
            try:
                return raw_body.decode(enc)
            except UnicodeDecodeError:
                continue
        try:
            return raw_body.decode(errors="replace")
        except Exception:
            return ""

    def _safe_rtf_to_text(self, body: str):
        """Convert RTF to text with graceful degradation."""
        if not body:
            return None
        try:
            from striprtf.striprtf import rtf_to_text
            text_val = rtf_to_text(body).strip()
            if text_val:
                return text_val
        except Exception:
            pass
        # Fallback: basic control-word stripping
        try:
            import re
            text_basic = re.sub(r'\\[a-z]+\\d*\\s?', '', body)
            text_basic = re.sub(r'\{[^}]*\}', '', text_basic)
            text_basic = text_basic.replace('\\par', '\n').replace('\\line', '\n')
            text_basic = ' '.join(text_basic.split()).strip()
            return text_basic or None
        except Exception:
            return None



