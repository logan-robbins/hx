/* A small DOM for node, enough to run src/hx/ui/static/app.js headlessly.
 *
 * ui-8 restored autodev's UI, which builds the shell with `innerHTML` strings,
 * so the shim parses HTML now instead of only accepting nodes: `buildDocument`
 * takes the real `static/index.html`, so the harness runs against the page the
 * server actually serves. Anything app.js touches that is missing here throws,
 * which is still the point.
 *
 * The selector engine covers what app.js uses: tag, `.class`, `#id`,
 * `[attr]`, `[attr="value"]`, compounds of those, descendant combinators and
 * comma lists. Anything else throws rather than silently matching nothing. */

"use strict";

/* HTML's void elements only. The SVG shapes app.js emits all close themselves
 * with `/>`, and `<path>` may carry a `<title>`, so they are not void here. */
const VOID = new Set([
  "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
  "param", "source", "track", "wbr",
]);

const ENTITIES = { amp: "&", lt: "<", gt: ">", quot: '"', apos: "'", nbsp: " " };

function decode(text) {
  return String(text).replace(/&(#x?[0-9a-fA-F]+|[a-zA-Z]+);/g, (whole, body) => {
    if (body[0] === "#") {
      const code = body[1] === "x" || body[1] === "X" ? parseInt(body.slice(2), 16) : parseInt(body.slice(1), 10);
      return Number.isFinite(code) ? String.fromCodePoint(code) : whole;
    }
    return Object.hasOwn(ENTITIES, body) ? ENTITIES[body] : whole;
  });
}

const escapeText = (text) =>
  String(text).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" })[c]);

class Text {
  constructor(data) {
    this.nodeType = 3;
    this.data = String(data);
    this.parentNode = null;
  }
  get textContent() {
    return this.data;
  }
  get outerHTML() {
    return escapeText(this.data);
  }
  descendants() {
    return [];
  }
}

let counter = 0;

class Node {
  constructor(tag) {
    this.nodeType = 1;
    this.tagName = String(tag).toUpperCase();
    this.childNodes = [];
    this.attrs = new Map();
    this.dataset = {};
    this.listeners = {};
    this.parentNode = null;
    this.hidden = false;
    this.value = "";
    this.disabled = false;
    this.selectionStart = 0;
    this.scrollTop = 0;
    this.scrollLeft = 0;
    this.clientWidth = 900;
    this.clientHeight = 450;
    this.offsetWidth = 900;
    this.offsetHeight = 450;
    this.ownerDocument = null;
    this.uid = ++counter;
  }

  /* -- attributes ------------------------------------------------------ */
  get className() {
    return this.attrs.get("class") || "";
  }
  set className(value) {
    this.setAttribute("class", value);
  }
  setAttribute(name, value) {
    this.attrs.set(name, String(value));
    if (name.startsWith("data-")) {
      this.dataset[name.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = String(value);
    }
    if (name === "hidden") this.hidden = true;
  }
  getAttribute(name) {
    return this.attrs.has(name) ? this.attrs.get(name) : null;
  }
  hasAttribute(name) {
    return this.attrs.has(name);
  }
  removeAttribute(name) {
    this.attrs.delete(name);
    if (name.startsWith("data-")) delete this.dataset[name.slice(5)];
  }
  get classList() {
    const node = this;
    const parts = () => node.className.split(/\s+/).filter(Boolean);
    return {
      contains: (name) => parts().includes(name),
      add(name) {
        if (!parts().includes(name)) node.className = parts().concat(name).join(" ");
      },
      remove(name) {
        node.className = parts().filter((p) => p !== name).join(" ");
      },
      toggle(name, force) {
        const want = force === undefined ? !parts().includes(name) : Boolean(force);
        const kept = parts().filter((p) => p !== name);
        node.className = (want ? kept.concat(name) : kept).join(" ");
      },
    };
  }

  /* -- children -------------------------------------------------------- */
  get children() {
    return this.childNodes.filter((n) => n.nodeType === 1);
  }
  append(...nodes) {
    for (const node of nodes) {
      if (node === null || node === undefined || node === false) continue;
      const child = typeof node === "string" || typeof node === "number" ? new Text(node) : node;
      if (!(child instanceof Node) && !(child instanceof Text)) {
        throw new TypeError("domshim: appended a " + Object.prototype.toString.call(node));
      }
      child.parentNode = this;
      this.childNodes.push(child);
    }
  }
  replaceChildren(...nodes) {
    this.childNodes = [];
    this.append(...nodes);
  }
  contains(other) {
    for (let n = other; n; n = n.parentNode) if (n === this) return true;
    return false;
  }
  get isConnected() {
    return true;
  }
  get offsetParent() {
    return this.parentNode;
  }
  get offsetLeft() {
    return Number(String(this.getAttribute("style") || "").match(/left:(-?[\d.]+)px/)?.[1] || 0);
  }
  get offsetTop() {
    return Number(String(this.getAttribute("style") || "").match(/top:(-?[\d.]+)px/)?.[1] || 0);
  }

  /* -- content --------------------------------------------------------- */
  set textContent(value) {
    this.childNodes = [];
    if (value !== null && value !== undefined && value !== "") this.append(new Text(value));
  }
  get textContent() {
    return this.childNodes.map((n) => n.textContent).join("");
  }
  set innerHTML(html) {
    this.childNodes = [];
    for (const node of parse(String(html))) this.append(node);
  }
  get innerHTML() {
    return this.childNodes.map((n) => n.outerHTML).join("");
  }
  get outerHTML() {
    const attrs = [...this.attrs].map(([k, v]) => ` ${k}="${String(v).replace(/"/g, "&quot;")}"`).join("");
    const tag = this.tagName.toLowerCase();
    if (VOID.has(tag)) return `<${tag}${attrs}>`;
    return `<${tag}${attrs}>${this.innerHTML}</${tag}>`;
  }

  /* -- traversal ------------------------------------------------------- */
  descendants() {
    const out = [];
    for (const child of this.childNodes) {
      if (child.nodeType !== 1) continue;
      out.push(child, ...child.descendants());
    }
    return out;
  }
  matches(selector) {
    return compile(selector).some((chain) => matchChain(this, chain));
  }
  closest(selector) {
    for (let n = this; n; n = n.parentNode) if (n.nodeType === 1 && n.matches(selector)) return n;
    return null;
  }
  querySelectorAll(selector) {
    return this.descendants().filter((n) => n.matches(selector));
  }
  querySelector(selector) {
    return this.querySelectorAll(selector)[0] || null;
  }

  /* -- events and focus ------------------------------------------------ */
  addEventListener(type, handler) {
    (this.listeners[type] = this.listeners[type] || []).push(handler);
  }
  focus() {
    if (this.ownerDocument) this.ownerDocument.activeElement = this;
  }
  setSelectionRange(start) {
    this.selectionStart = start;
  }
  get id() {
    return this.getAttribute("id") || "";
  }
}

/* -- selectors --------------------------------------------------------- */

const SIMPLE = /^(?:([a-zA-Z][\w-]*)|\.([\w-]+)|#([\w-]+)|\[([\w-]+)(?:([~^]?=)"([^"]*)")?\])/;

function compileSimple(text) {
  const parts = [];
  let rest = text;
  while (rest) {
    const match = SIMPLE.exec(rest);
    if (!match) throw new Error("domshim: unsupported selector fragment " + text);
    if (match[1]) parts.push({ tag: match[1].toUpperCase() });
    else if (match[2]) parts.push({ cls: match[2] });
    else if (match[3]) parts.push({ id: match[3] });
    else parts.push({ attr: match[4], op: match[5] || null, value: match[6] });
    rest = rest.slice(match[0].length);
  }
  return parts;
}

const cache = new Map();

function compile(selector) {
  if (cache.has(selector)) return cache.get(selector);
  const chains = selector
    .split(",")
    .map((part) => part.trim())
    .filter(Boolean)
    .map((part) => part.split(/\s+/).map(compileSimple));
  cache.set(selector, chains);
  return chains;
}

function matchSimple(node, parts) {
  return parts.every((part) => {
    if (part.tag) return node.tagName === part.tag;
    if (part.cls) return node.classList.contains(part.cls);
    if (part.id) return node.getAttribute("id") === part.id;
    const value = node.getAttribute(part.attr);
    if (value === null) return false;
    if (!part.op) return true;
    if (part.op === "=") return value === part.value;
    if (part.op === "~=") return value.split(/\s+/).includes(part.value);
    return value.startsWith(part.value);
  });
}

/** A descendant chain, matched right to left. */
function matchChain(node, chain) {
  if (!matchSimple(node, chain[chain.length - 1])) return false;
  let i = chain.length - 2;
  let current = node.parentNode;
  while (i >= 0) {
    if (!current || current.nodeType !== 1) return false;
    if (matchSimple(current, chain[i])) i--;
    current = current.parentNode;
  }
  return true;
}

/* -- the parser -------------------------------------------------------- */

const TAG = /<(\/?)([a-zA-Z][\w:-]*)((?:[^>"']|"[^"]*"|'[^']*')*?)(\/?)>/g;
const ATTR = /([\w:-]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+)))?/g;

function parse(html) {
  const root = new Node("root");
  const stack = [root];
  let index = 0;
  TAG.lastIndex = 0;
  let match;
  const text = (raw) => {
    if (!raw) return;
    stack[stack.length - 1].append(new Text(decode(raw)));
  };
  const clean = html.replace(/<!--[\s\S]*?-->/g, "").replace(/<!doctype[^>]*>/gi, "");
  while ((match = TAG.exec(clean)) !== null) {
    text(clean.slice(index, match.index));
    index = TAG.lastIndex;
    const [, closing, name, rawAttrs, selfClose] = match;
    const tag = name.toLowerCase();
    if (closing) {
      for (let i = stack.length - 1; i > 0; i--) {
        if (stack[i].tagName === tag.toUpperCase()) {
          stack.length = i;
          break;
        }
      }
      continue;
    }
    const node = new Node(tag);
    ATTR.lastIndex = 0;
    let attr;
    while ((attr = ATTR.exec(rawAttrs)) !== null) {
      const value = attr[2] ?? attr[3] ?? attr[4] ?? "";
      node.setAttribute(attr[1], decode(value));
    }
    stack[stack.length - 1].append(node);
    if (!selfClose && !VOID.has(tag)) stack.push(node);
  }
  text(clean.slice(index));
  return root.childNodes;
}

/* -- the document ------------------------------------------------------ */

function buildDocument(indexHtml) {
  const body = new Node("body");
  const inside = /<body[^>]*>([\s\S]*)<\/body>/i.exec(indexHtml);
  body.innerHTML = inside ? inside[1] : indexHtml;
  const doc = {
    body,
    activeElement: null,
    listeners: {},
    getElementById(id) {
      // null, like the real DOM: app.js uses `$("graph-viewport")` as an
      // existence check for a node only some pages draw.
      return body.querySelectorAll('[id="' + id + '"]')[0] || null;
    },
    createElement: (tag) => {
      const node = new Node(tag);
      node.ownerDocument = doc;
      return node;
    },
    querySelectorAll: (selector) => body.querySelectorAll(selector),
    querySelector: (selector) => body.querySelector(selector),
    addEventListener(type, handler) {
      (doc.listeners[type] = doc.listeners[type] || []).push(handler);
    },
    contains: (node) => body.contains(node),
  };
  const attach = (node) => {
    node.ownerDocument = doc;
    for (const child of node.descendants()) child.ownerDocument = doc;
  };
  attach(body);
  doc.attach = attach;
  return doc;
}

/** Fire an event at `node`, bubbling to the document, the way a click does. */
function fire(doc, node, type, extra = {}) {
  const event = Object.assign(
    { type, target: node, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; } },
    extra,
  );
  for (let n = node; n; n = n.parentNode) {
    for (const handler of (n.listeners && n.listeners[type]) || []) handler(event);
  }
  for (const handler of doc.listeners[type] || []) handler(event);
  return event;
}

module.exports = { Node, Text, parse, buildDocument, fire, decode };
