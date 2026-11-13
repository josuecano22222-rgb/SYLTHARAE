/**
 * Declarative event handlers without inline script (RES-CSP-01).
 *
 * The templates used to wire controls with inline handler attributes -
 * ``onclick="applyFilters()"`` - which only run when the Content-Security-Policy
 * allows ``script-src 'unsafe-inline'``. That allowance also lets any injected
 * markup run script, so it defeated most of the policy.
 *
 * The same wiring is now written ``data-on-click="applyFilters()"`` and this
 * module runs it: one delegated listener per event type on the document finds
 * the element carrying ``data-on-<type>`` and executes its expression with
 * ``this`` bound to that element and ``event`` bound to the event - the same
 * contract an inline handler had.
 *
 * The expression is never passed to ``eval``/``Function`` (the policy forbids
 * both). It is parsed by the small interpreter below, which accepts only the
 * subset the templates use: calls (``fn(a, b)``, ``obj.method()``,
 * ``fn?.()``), member and index access, string/number/boolean/null literals,
 * JSON-shaped arrays and objects, ``this``, ``event``, ``! - + === !== == !=
 * && || ?:``, ``if (...) stmt``, ``{ ... }`` blocks, ``return false`` (which
 * prevents the default action, as it did inline) and assignment to a
 * ``.style.<property>`` (hover and image-fallback effects). Anything else is a
 * syntax error, reported to the console, and nothing runs.
 *
 * Defence in depth: this is still a way to call page functions from markup, so
 * it refuses the names that would turn an HTML-injection bug back into script
 * execution - ``eval``, ``Function``, string timers, ``constructor``/
 * ``__proto__``, HTML-writing sinks - both by name and by identity at call
 * time. Output escaping remains the primary control against injection.
 *
 * Loaded synchronously in ``<head>`` (it is small) so the listeners exist
 * before the first control is drawn.
 */
(function (global) {
    'use strict';

    const ATTRIBUTE_PREFIX = 'data-on-';
    const BULK_HANDLER = 'data-bulk-handler';

    /** Event types wired through ``data-on-<type>``. */
    const EVENTS = ['click', 'dblclick', 'change', 'input', 'keyup', 'keydown',
        'keypress', 'mouseover', 'mouseout', 'submit', 'blur', 'focus', 'error'];

    /** These do not bubble; they are caught in the capture phase, at the target only. */
    const NON_BUBBLING = new Set(['blur', 'focus', 'error']);

    /** Property and identifier names an expression may never touch. */
    const BLOCKED_NAMES = new Set([
        'constructor', '__proto__', 'prototype', '__defineGetter__', '__defineSetter__',
        '__lookupGetter__', '__lookupSetter__', 'eval', 'Function', 'AsyncFunction',
        'GeneratorFunction', 'setTimeout', 'setInterval', 'setImmediate', 'execScript',
        'importScripts', 'Reflect', 'Proxy', 'Object', 'WebAssembly', 'Worker',
        'SharedWorker', 'globalThis', 'self', 'top', 'parent', 'frames', 'opener',
        'innerHTML', 'outerHTML', 'srcdoc', 'insertAdjacentHTML', 'write', 'writeln',
        'createContextualFragment', 'setHTMLUnsafe', 'parseHTMLUnsafe', 'fetch',
        'XMLHttpRequest', 'sendBeacon', 'import', 'cookie',
    ]);

    // ------------------------------------------------------------------
    // Tokenizer
    // ------------------------------------------------------------------
    const PUNCTUATORS = ['===', '!==', '?.', '==', '!=', '&&', '||',
        '(', ')', '[', ']', '{', '}', ',', '.', ';', ':', '?', '!', '-', '+', '='];

    function tokenize(source) {
        const tokens = [];
        let i = 0;
        const n = source.length;
        while (i < n) {
            const ch = source[i];
            if (/\s/.test(ch)) { i += 1; continue; }
            if (ch === '"' || ch === "'") {
                let value = '';
                let j = i + 1;
                let closed = false;
                while (j < n) {
                    const c = source[j];
                    if (c === '\\') {
                        const next = source[j + 1];
                        const escapes = {n: '\n', t: '\t', r: '\r', b: '\b', f: '\f', v: '\v', 0: '\0'};
                        if (next === 'u' && /^[0-9a-fA-F]{4}$/.test(source.substr(j + 2, 4))) {
                            value += String.fromCharCode(parseInt(source.substr(j + 2, 4), 16));
                            j += 6;
                            continue;
                        }
                        value += Object.prototype.hasOwnProperty.call(escapes, next) ? escapes[next] : next;
                        j += 2;
                        continue;
                    }
                    if (c === ch) { closed = true; break; }
                    value += c;
                    j += 1;
                }
                if (!closed) throw new SyntaxError('unterminated string');
                tokens.push({type: 'str', value});
                i = j + 1;
                continue;
            }
            const num = /^(?:\d+\.?\d*(?:[eE][+-]?\d+)?|\.\d+(?:[eE][+-]?\d+)?)/.exec(source.slice(i));
            if (num) {
                tokens.push({type: 'num', value: Number(num[0])});
                i += num[0].length;
                continue;
            }
            const ident = /^[A-Za-z_$][\w$]*/.exec(source.slice(i));
            if (ident) {
                tokens.push({type: 'ident', value: ident[0]});
                i += ident[0].length;
                continue;
            }
            const punct = PUNCTUATORS.find((p) => source.startsWith(p, i)
                // "a?.5:b" is a ternary, not optional chaining (as in JS)
                && !(p === '?.' && /\d/.test(source[i + 2] || '')));
            if (!punct) throw new SyntaxError(`unexpected character ${JSON.stringify(ch)}`);
            tokens.push({type: 'punct', value: punct});
            i += punct.length;
        }
        tokens.push({type: 'eof', value: null});
        return tokens;
    }

    // ------------------------------------------------------------------
    // Parser -> small AST
    // ------------------------------------------------------------------
    const KEYWORD_VALUES = {true: true, false: false, null: null, undefined: undefined};
    const RESERVED = new Set(['if', 'else', 'return', 'var', 'let', 'const', 'function',
        'new', 'delete', 'typeof', 'void', 'in', 'instanceof', 'while', 'for', 'do',
        'class', 'with', 'yield', 'await', 'async', 'throw', 'try', 'catch', 'switch']);

    function parse(source) {
        const tokens = tokenize(String(source));
        let pos = 0;
        const peek = () => tokens[pos];
        const isPunct = (value) => peek().type === 'punct' && peek().value === value;
        const isWord = (value) => peek().type === 'ident' && peek().value === value;
        function expect(value) {
            if (!isPunct(value)) {
                throw new SyntaxError(`expected ${JSON.stringify(value)} but found ${JSON.stringify(peek().value)}`);
            }
            pos += 1;
        }

        function program(terminator) {
            const body = [];
            while (peek().type !== 'eof' && !(terminator && isPunct(terminator))) {
                if (isPunct(';')) { pos += 1; continue; }
                body.push(statement());
                if (!isPunct(';') && peek().type !== 'eof' && !(terminator && isPunct(terminator))
                        && !(body[body.length - 1].type === 'if' || body[body.length - 1].type === 'block')) {
                    throw new SyntaxError(`unexpected ${JSON.stringify(peek().value)}`);
                }
            }
            return body;
        }

        function statement() {
            if (isPunct('{')) {
                pos += 1;
                const body = program('}');
                expect('}');
                return {type: 'block', body};
            }
            if (isWord('if')) {
                pos += 1;
                expect('(');
                const test = expression();
                expect(')');
                const consequent = statement();
                let alternate = null;
                if (isWord('else')) { pos += 1; alternate = statement(); }
                return {type: 'if', test, consequent, alternate};
            }
            if (isWord('return')) {
                pos += 1;
                const argument = (isPunct(';') || isPunct('}') || peek().type === 'eof') ? null : expression();
                return {type: 'return', argument};
            }
            const expr = expression();
            if (isPunct('=')) {
                pos += 1;
                const value = expression();
                const target = expr;
                const styleTarget = target.type === 'member' && !target.optional
                    && target.object.type === 'member' && !target.object.computed
                    && target.object.property.value === 'style';
                if (!styleTarget) throw new SyntaxError('only .style.<property> may be assigned');
                return {type: 'assign', target, value};
            }
            return {type: 'expr', expression: expr};
        }

        function expression() {
            const test = binary(0);
            if (isPunct('?')) {
                pos += 1;
                const consequent = expression();
                expect(':');
                const alternate = expression();
                return {type: 'conditional', test, consequent, alternate};
            }
            return test;
        }

        const PRECEDENCE = [['||'], ['&&'], ['===', '!==', '==', '!='], ['+', '-']];
        function binary(level) {
            if (level >= PRECEDENCE.length) return unary();
            let left = binary(level + 1);
            while (peek().type === 'punct' && PRECEDENCE[level].includes(peek().value)) {
                const operator = peek().value;
                pos += 1;
                left = {type: 'binary', operator, left, right: binary(level + 1)};
            }
            return left;
        }

        function unary() {
            if (isPunct('!') || isPunct('-') || isPunct('+')) {
                const operator = peek().value;
                pos += 1;
                return {type: 'unary', operator, argument: unary()};
            }
            return postfix(primary());
        }

        function args() {
            const list = [];
            expect('(');
            while (!isPunct(')')) {
                list.push(expression());
                if (!isPunct(')')) expect(',');
            }
            expect(')');
            return list;
        }

        function propertyName() {
            const token = peek();
            if (token.type !== 'ident') throw new SyntaxError('expected a property name');
            pos += 1;
            return {type: 'literal', value: token.value};
        }

        function postfix(node) {
            for (;;) {
                if (isPunct('.')) {
                    pos += 1;
                    node = {type: 'member', object: node, property: propertyName(), computed: false, optional: false};
                } else if (isPunct('?.')) {
                    pos += 1;
                    if (isPunct('(')) {
                        node = {type: 'call', callee: node, args: args(), optional: true};
                    } else if (isPunct('[')) {
                        pos += 1;
                        const property = expression();
                        expect(']');
                        node = {type: 'member', object: node, property, computed: true, optional: true};
                    } else {
                        node = {type: 'member', object: node, property: propertyName(), computed: false, optional: true};
                    }
                } else if (isPunct('[')) {
                    pos += 1;
                    const property = expression();
                    expect(']');
                    node = {type: 'member', object: node, property, computed: true, optional: false};
                } else if (isPunct('(')) {
                    node = {type: 'call', callee: node, args: args(), optional: false};
                } else {
                    return node;
                }
            }
        }

        function primary() {
            const token = peek();
            if (token.type === 'num' || token.type === 'str') {
                pos += 1;
                return {type: 'literal', value: token.value};
            }
            if (token.type === 'ident') {
                pos += 1;
                if (Object.prototype.hasOwnProperty.call(KEYWORD_VALUES, token.value)) {
                    return {type: 'literal', value: KEYWORD_VALUES[token.value]};
                }
                if (token.value === 'this') return {type: 'this'};
                if (token.value === 'event') return {type: 'event'};
                if (RESERVED.has(token.value)) throw new SyntaxError(`${token.value} is not supported`);
                return {type: 'identifier', name: token.value};
            }
            if (isPunct('(')) {
                pos += 1;
                const inner = expression();
                expect(')');
                return inner;
            }
            if (isPunct('[')) {
                pos += 1;
                const elements = [];
                while (!isPunct(']')) {
                    elements.push(expression());
                    if (!isPunct(']')) expect(',');
                }
                expect(']');
                return {type: 'array', elements};
            }
            if (isPunct('{')) {
                pos += 1;
                const properties = [];
                while (!isPunct('}')) {
                    const key = peek();
                    if (!['ident', 'str', 'num'].includes(key.type)) throw new SyntaxError('bad object key');
                    pos += 1;
                    expect(':');
                    properties.push({key: String(key.value), value: expression()});
                    if (!isPunct('}')) expect(',');
                }
                expect('}');
                return {type: 'object', properties};
            }
            throw new SyntaxError(`unexpected ${JSON.stringify(token.value)}`);
        }

        const body = program(null);
        if (peek().type !== 'eof') throw new SyntaxError(`unexpected ${JSON.stringify(peek().value)}`);
        return {type: 'program', body};
    }

    // ------------------------------------------------------------------
    // Evaluator
    // ------------------------------------------------------------------
    const REFUSED = Symbol('refused');
    const SHORT_CIRCUIT = Symbol('short-circuit');

    function refusedValues() {
        return [global.eval, global.Function, global.setTimeout, global.setInterval,
            global.setImmediate, global.fetch, global.XMLHttpRequest]
            .filter((value) => typeof value === 'function');
    }

    function checkName(name) {
        if (BLOCKED_NAMES.has(String(name))) {
            throw new TypeError(`"${name}" is not allowed in a declarative handler`);
        }
    }

    function resolveIdentifier(name) {
        checkName(name);
        if (name === 'window') return global;
        if (!(name in global)) throw new ReferenceError(`${name} is not defined`);
        return global[name];
    }

    function evaluate(node, scope) {
        switch (node.type) {
        case 'literal': return node.value;
        case 'this': return scope.self;
        case 'event': return scope.event;
        case 'identifier': return resolveIdentifier(node.name);
        case 'array': return node.elements.map((el) => evaluate(el, scope));
        case 'object': {
            const out = {};
            node.properties.forEach(({key, value}) => {
                checkName(key);
                out[key] = evaluate(value, scope);
            });
            return out;
        }
        case 'unary': {
            const value = evaluate(node.argument, scope);
            if (node.operator === '!') return !value;
            if (node.operator === '-') return -value;
            return +value;
        }
        case 'binary': {
            if (node.operator === '&&') {
                const left = evaluate(node.left, scope);
                return left ? evaluate(node.right, scope) : left;
            }
            if (node.operator === '||') {
                const left = evaluate(node.left, scope);
                return left ? left : evaluate(node.right, scope);
            }
            const left = evaluate(node.left, scope);
            const right = evaluate(node.right, scope);
            switch (node.operator) {
            case '===': return left === right;
            case '!==': return left !== right;
            // eslint-disable-next-line eqeqeq
            case '==': return left == right;
            // eslint-disable-next-line eqeqeq
            case '!=': return left != right;
            case '+': return left + right;
            case '-': return left - right;
            default: throw new SyntaxError(node.operator);
            }
        }
        case 'conditional':
            return evaluate(node.test, scope) ? evaluate(node.consequent, scope) : evaluate(node.alternate, scope);
        case 'member':
        case 'call': {
            const value = chain(node, scope).value;
            return value === SHORT_CIRCUIT ? undefined : value;
        }
        default:
            throw new SyntaxError(`cannot evaluate ${node.type}`);
        }
    }

    /** Evaluate a member/call chain, keeping the receiver for method calls. */
    function chain(node, scope) {
        if (node.type === 'member') {
            const base = chain(node.object, scope).value;
            if (base === SHORT_CIRCUIT) return {value: SHORT_CIRCUIT};
            if (base === null || base === undefined) {
                if (node.optional) return {value: SHORT_CIRCUIT};
                throw new TypeError('cannot read a property of null or undefined');
            }
            const key = node.computed ? evaluate(node.property, scope) : node.property.value;
            checkName(key);
            return {value: base[key], receiver: base};
        }
        if (node.type === 'call') {
            // ``fn?.()`` is written to mean "if this page defines fn": a name no
            // script declared is treated as undefined, not a ReferenceError.
            if (node.optional && node.callee.type === 'identifier' && !(node.callee.name in global)) {
                checkName(node.callee.name);
                return {value: SHORT_CIRCUIT};
            }
            const callee = chain(node.callee, scope);
            if (callee.value === SHORT_CIRCUIT) return {value: SHORT_CIRCUIT};
            if (callee.value === null || callee.value === undefined) {
                if (node.optional) return {value: SHORT_CIRCUIT};
                throw new TypeError('not a function');
            }
            if (typeof callee.value !== 'function') throw new TypeError('not a function');
            if (refusedValues().includes(callee.value)) throw new TypeError('refused function');
            const argValues = node.args.map((arg) => evaluate(arg, scope));
            return {value: callee.value.apply(callee.receiver === undefined ? global : callee.receiver, argValues)};
        }
        return {value: evaluate(node, scope)};
    }

    function executeStatements(body, scope) {
        for (const statement of body) {
            if (execute(statement, scope) === REFUSED) return REFUSED;
        }
        return undefined;
    }

    /** Run one statement; returns REFUSED after `return` so the caller stops. */
    function execute(statement, scope) {
        switch (statement.type) {
        case 'block': return executeStatements(statement.body, scope);
        case 'if':
            if (evaluate(statement.test, scope)) return execute(statement.consequent, scope);
            return statement.alternate ? execute(statement.alternate, scope) : undefined;
        case 'return': {
            const value = statement.argument ? evaluate(statement.argument, scope) : undefined;
            if (value === false && scope.event && typeof scope.event.preventDefault === 'function') {
                scope.event.preventDefault();
            }
            return REFUSED;
        }
        case 'assign': {
            const styleObject = chain(statement.target.object, scope).value;
            const key = statement.target.property.value;
            checkName(key);
            if (styleObject === null || styleObject === undefined || styleObject === SHORT_CIRCUIT) {
                throw new TypeError('cannot assign to a missing style');
            }
            styleObject[key] = evaluate(statement.value, scope);
            return undefined;
        }
        case 'expr':
            evaluate(statement.expression, scope);
            return undefined;
        default:
            throw new SyntaxError(`cannot execute ${statement.type}`);
        }
    }

    const cache = new Map();

    function compile(source) {
        if (!cache.has(source)) {
            let program;
            try {
                program = parse(source);
            } catch (error) {
                program = error;
            }
            cache.set(source, program);
        }
        const program = cache.get(source);
        if (program instanceof Error) throw program;
        return program;
    }

    function report(error, source) {
        const message = `data-on handler failed: ${source}`;
        if (global.console && typeof global.console.error === 'function') {
            global.console.error(message, error);
        }
    }

    /** Run ``source`` as a handler for ``self`` and ``event``. */
    function run(source, self, event) {
        try {
            executeStatements(compile(source).body, {self, event});
        } catch (error) {
            report(error, source);
        }
    }

    function dispatch(event) {
        const attribute = ATTRIBUTE_PREFIX + event.type;
        let node = event.target;
        if (node && node.nodeType === 3) node = node.parentElement;   // text node
        if (NON_BUBBLING.has(event.type)) {
            if (node && typeof node.getAttribute === 'function' && node.hasAttribute(attribute)) {
                run(node.getAttribute(attribute), node, event);
            }
            return;
        }
        while (node && typeof node.getAttribute === 'function') {
            // A bulk action carries its handler apart from data-on-click and
            // runs only while enabled, i.e. once a selection gives it a scope.
            if (event.type === 'click' && node.hasAttribute(BULK_HANDLER)) {
                if (!node.disabled && node.getAttribute('aria-disabled') !== 'true') {
                    run(node.getAttribute(BULK_HANDLER), node, event);
                }
                break;
            }
            if (node.hasAttribute(attribute)) {
                run(node.getAttribute(attribute), node, event);
                if (event.cancelBubble) break;   // the handler stopped propagation
            }
            node = node.parentElement;
        }
    }

    function install(doc) {
        if (!doc || typeof doc.addEventListener !== 'function' || doc.__declarativeEventsInstalled) return;
        doc.__declarativeEventsInstalled = true;
        EVENTS.forEach((type) => {
            doc.addEventListener(type, dispatch, NON_BUBBLING.has(type));
        });
    }

    global.SyltharaeDeclarativeEvents = {EVENTS, ATTRIBUTE_PREFIX, BLOCKED_NAMES, parse, run, dispatch, install};
    install(global.document);
})(typeof window !== 'undefined' ? window : globalThis);
