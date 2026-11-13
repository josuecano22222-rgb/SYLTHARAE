// A minimal DOM for running a page module inside node:vm (shared by the
// page smoke tests). Counts innerHTML writes; append(null) throws because a
// browser would insert the text "null".
export const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
export const counters = { innerHtmlWrites: 0 };

export function makeDom() {
  const byId = {};
  function textNode(v) {
    return { nodeType: 3, _text: String(v), get textContent() { return this._text; },
             get outerHTML() { return esc(this._text); } };
  }
  function el(tag = 'div') {
    const e = {
      nodeType: 1, tagName: tag.toUpperCase(), children: [], listeners: {}, attrs: {},
      className: '', value: '', checked: false, disabled: false, dataset: {},
      classList: {
        _set: new Set(),
        add(c) { this._set.add(c); }, remove(c) { this._set.delete(c); },
        toggle(c, on) { if (on === undefined ? !this._set.has(c) : on) this._set.add(c); else this._set.delete(c); },
        contains(c) { return this._set.has(c); },
      },
      setAttribute(k, v) {
        this.attrs[k] = String(v);
        if (k === 'id') byId[v] = this;
        if (k.startsWith('data-')) this.dataset[k.slice(5).replace(/-(\w)/g, (_, c) => c.toUpperCase())] = String(v);
        if (k === 'checked') this.checked = true;
      },
      addEventListener(t, f) { (this.listeners[t] ||= []).push(f); },
      append(...c) {
        for (const x of c) {
          // A browser inserts the text "null"/"undefined" for these: a page bug.
          if (x === null || x === undefined) throw new Error(`append(${x}) would insert the text "${x}"`);
          this.children.push(typeof x === 'string' ? textNode(x) : x);
        }
      },
      replaceChildren(...c) { this.children = []; this.append(...c); },
      get textContent() { return this.children.map((c) => c.textContent).join(''); },
      set textContent(v) { this.children = [textNode(v)]; },
      get innerHTML() { return this.children.map((c) => c.outerHTML).join(''); },
      set innerHTML(v) { counters.innerHtmlWrites += 1; },
      get outerHTML() {
        const a = Object.entries(this.attrs).map(([k, v]) => ` ${k}="${esc(v)}"`).join('');
        return `<${tag}${a} class="${this.className}">${this.innerHTML}</${tag}>`;
      },
      querySelector(sel) { return this.querySelectorAll(sel)[0] || null; },
      querySelectorAll(sel) {
        const alts = sel.split(',').map((s) => s.trim());
        const match = (n) => n.nodeType === 1 && alts.some((s) => {
          if (s.startsWith('#')) return n.attrs.id === s.slice(1);
          const m = s.match(/^(\w+)(?:\[type=(\w+)\])?$/);
          if (m) return n.tagName === m[1].toUpperCase() && (!m[2] || n.attrs.type === m[2]);
          if (s.startsWith('[data-param]')) return 'param' in n.dataset;
          return false;
        });
        const out = [];
        const walk = (n) => { for (const c of n.children || []) { if (match(c)) out.push(c); walk(c); } };
        walk(this);
        return out;
      },
    };
    return e;
  }
  const document = {
    getElementById(id) { if (!byId[id]) { byId[id] = el(); byId[id].attrs.id = id; } return byId[id]; },
    createElement: el,
    createTextNode: textNode,
    querySelectorAll(sel) {
      const m = sel.match(/^#(\w+) (.*)$/);
      return m ? document.getElementById(m[1]).querySelectorAll(m[2]) : [];
    },
  };
  return { document, byId };
}

