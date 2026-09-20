/* The smallest DOM the hx UI actually uses, so the render functions in
 * src/hx/ui/static/app.js can be exercised in node without a browser. Anything
 * app.js touches that is missing here throws, which is the point. */

"use strict";

class Node {
  constructor(tag) {
    this.tagName = String(tag).toUpperCase();
    this.children = [];
    this.attrs = {};
    this.className = "";
    this.dataset = {};
    this.listeners = {};
    this.own = "";
    this.hidden = false;
    this.value = "";
  }

  set textContent(value) {
    this.own = value === null || value === undefined ? "" : String(value);
    this.children = [];
  }

  get textContent() {
    return this.own + this.children.map((c) => (typeof c === "string" ? c : c.textContent)).join("");
  }

  setAttribute(name, value) {
    this.attrs[name] = String(value);
    if (name.startsWith("data-")) this.dataset[name.slice(5)] = String(value);
  }

  getAttribute(name) {
    return Object.hasOwn(this.attrs, name) ? this.attrs[name] : null;
  }

  removeAttribute(name) {
    delete this.attrs[name];
    if (name.startsWith("data-")) delete this.dataset[name.slice(5)];
  }

  append(...nodes) {
    for (const node of nodes) if (node !== null && node !== undefined) this.children.push(node);
  }

  replaceChildren(...nodes) {
    this.own = "";
    this.children = nodes.filter((n) => n !== null && n !== undefined);
  }

  addEventListener(type, handler) {
    this.listeners[type] = handler;
  }

  get classList() {
    const node = this;
    return {
      toggle(name, force) {
        const has = node.className.split(/\s+/).includes(name);
        const want = force === undefined ? !has : Boolean(force);
        const parts = node.className.split(/\s+/).filter((p) => p && p !== name);
        if (want) parts.push(name);
        node.className = parts.join(" ");
      },
      contains: (name) => node.className.split(/\s+/).includes(name),
    };
  }

  closest(selector) {
    if (selector === "button[data-view]") {
      return this.tagName === "BUTTON" && this.dataset.view ? this : null;
    }
    if (selector === "button[data-open]") {
      return this.tagName === "BUTTON" && this.dataset.open ? this : null;
    }
    throw new Error("domshim: unsupported closest selector " + selector);
  }

  descendants() {
    const out = [];
    for (const child of this.children) {
      if (typeof child === "string") continue;
      if (!(child instanceof Node)) {
        throw new TypeError("domshim: appended a " + Object.prototype.toString.call(child) + ", not a node");
      }
      out.push(child, ...child.descendants());
    }
    return out;
  }

  querySelectorAll(selector) {
    if (selector === "#nav button") {
      return document.getElementById("nav").descendants().filter((n) => n.tagName === "BUTTON");
    }
    throw new Error("domshim: unsupported selector " + selector);
  }
}

const NAV = ["board", "orders", "archive", "agent", "partner"];

function buildDocument() {
  const nodes = {};
  for (const id of ["main", "banner", "live", "nav"]) nodes[id] = new Node("div");
  nodes.nav.tagName = "NAV";
  for (const name of NAV) {
    const button = new Node("button");
    button.setAttribute("data-view", name);
    nodes.nav.append(button);
  }
  return {
    getElementById: (id) => {
      if (!nodes[id]) throw new Error("domshim: no element #" + id);
      return nodes[id];
    },
    createElement: (tag) => new Node(tag),
    querySelectorAll: (selector) => {
      if (selector === "#nav button") {
        return nodes.nav.descendants().filter((n) => n.tagName === "BUTTON");
      }
      throw new Error("domshim: unsupported selector " + selector);
    },
    nodes,
  };
}

/** Flatten a rendered subtree to `{tag, class, text}` records. */
function collect(node, tag) {
  return node.descendants().filter((n) => n.tagName === tag.toUpperCase());
}

module.exports = { Node, buildDocument, collect, NAV };
