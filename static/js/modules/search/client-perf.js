/**
 * User-perceived search timings (TTFR, TTFC, TTCH).
 */
const marks = {
    requestStart: null,
    firstByte: null,
    responseComplete: null,
    firstResultRendered: null,
    firstContextRendered: null,
    hydrationComplete: null,
};

export function resetClientPerf() {
    Object.keys(marks).forEach((key) => {
        marks[key] = null;
    });
}

export function markClientPerf(name) {
    if (!Object.prototype.hasOwnProperty.call(marks, name)) return;
    marks[name] = performance.now();
}

export function clientPerfSnapshot() {
    const base = marks.requestStart || performance.now();
    const delta = (name) => (
        marks[name] != null ? Math.round(marks[name] - base) : null
    );
    return {
        ttfr_ms: delta('firstResultRendered'),
        ttfc_ms: delta('firstContextRendered'),
        ttch_ms: delta('hydrationComplete'),
        ttfb_ms: delta('firstByte'),
        response_ms: delta('responseComplete'),
    };
}

export function formatClientPerfLine(snapshot) {
    if (!snapshot) return '';
    const parts = [];
    if (snapshot.ttfr_ms != null) parts.push(`TTFR ${snapshot.ttfr_ms}ms`);
    if (snapshot.ttfc_ms != null) parts.push(`TTFC ${snapshot.ttfc_ms}ms`);
    if (snapshot.ttch_ms != null) parts.push(`TTCH ${snapshot.ttch_ms}ms`);
    return parts.join(' · ');
}
