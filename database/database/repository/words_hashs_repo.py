from .best_repo import BaseRepository
from ..queries.word_hash_queries import WordHashQueries
from core.serialization import pack_int_list, unpack_int_list
import logging

logger = logging.getLogger(__name__)

class WordsHashsRepository(BaseRepository):

    #: Rows per INSERT statement.  A large document can produce tens of
    #: thousands of (content, word) pairs; one statement per pair set would
    #: build a multi-megabyte SQL string and bind an unbounded parameter
    #: array, so the pairs are sent in bounded batches inside the caller's
    #: transaction.
    WORDS_HASHS_BATCH_SIZE = 5000

    def bulk_insert_words_hashs(self, tuple_hash_id_and_word_id_and_counts_and_position):
        """Insert ``(hash_id, word_id, word_count, positions)`` rows.

        Batched so the statement size stays bounded for very large documents.
        The rows are inserted inside the caller's transaction, so a failure in
        any batch rolls the whole document back.  The (hash_id, word_id)
        unique constraint (m0011) plus ON CONFLICT DO NOTHING makes this safe
        under concurrent ingestion of identical content.
        """
        if not tuple_hash_id_and_word_id_and_counts_and_position:
            return

        tuples_rows = list(tuple_hash_id_and_word_id_and_counts_and_position)
        batch_size = self.WORDS_HASHS_BATCH_SIZE

        for start in range(0, len(tuples_rows), batch_size):
            batch = tuples_rows[start:start + batch_size]
            placeholders = ",".join(["(%s,%s,%s,%s)"] * len(batch))
            query = WordHashQueries.insert_word_hash(placeholders)

            flat_values = [item for row in batch for item in row]
            self.execute(query, flat_values)

    def insert_words_hashs(self, hash_id, word_id, word_count, list_position_indexer):
        """Insert one word-content relationship.

        Returns the inserted ``hash_id`` when a new row was created, or
        ``None`` when the word was already indexed for this content.
        """
        position_byte = pack_int_list(list_position_indexer)

        params = (
            hash_id, word_id, word_count, position_byte,
            hash_id, word_id,
        )

        return self.execute(WordHashQueries.insert_word_hash_one(), params, True)

    def get_word_positions_by_hash(self, hash_id):
        """Get word positions for canonical content"""
        rows = self.execute(
            WordHashQueries.get_word_positions_by_hash(),
            (hash_id,),
            fetchall=True
        )
        result = {}
        for row in rows:
            if row and len(row) >= 2:
                word_id = row[0]
                position_byte = row[1]
                try:
                    positions = unpack_int_list(position_byte)
                    result[word_id] = positions
                except Exception as e:
                    logger.warning(
                        "Could not decode positions for word_id %s (hash %s): %s",
                        word_id, hash_id, e,
                    )
        return result
