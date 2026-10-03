// Helpers the Studio driver injects (ApplicationWorld: invisible to Studio's own scripts, the
// DOM and its events are shared).  Every call goes through window.__ag, defined once per page.
(function () {
  if (window.__ag && window.__ag.v === 4) return;
  function* walk(root) {
    // every element under root, descending into open shadow roots
    const stack = [root];
    while (stack.length) {
      const node = stack.pop();
      if (node.shadowRoot) stack.push(node.shadowRoot);
      const kids = node.children || [];
      for (let i = kids.length - 1; i >= 0; i--) stack.push(kids[i]);
      if (node.nodeType === 1) yield node;
    }
  }
  function visible(el) {
    if (!el || !el.isConnected) return false;
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) return false;
    const cs = getComputedStyle(el);
    return cs.visibility !== "hidden" && cs.display !== "none" && parseFloat(cs.opacity || "1") > 0.01;
  }
  function all(sel, root) {
    root = root || document;
    let found = [];
    try { found = Array.from(root.querySelectorAll(sel)); } catch (e) { return []; }
    if (found.length) return found;
    // not in the light DOM: search shadow roots
    const out = [];
    for (const el of walk(root)) {
      if (el.shadowRoot) {
        try { out.push(...el.shadowRoot.querySelectorAll(sel)); } catch (e) {}
      }
    }
    return out;
  }
  function q(sels, wantVisible, root) {
    for (const s of sels) {
      for (const el of all(s, root)) {
        if (!wantVisible || visible(el)) return el;
      }
    }
    return null;
  }
  function text(el) { return el ? (el.innerText || el.textContent || "").trim() : ""; }
  function enabled(el) {
    return el && !el.hasAttribute("disabled") && el.getAttribute("aria-disabled") !== "true";
  }
  function fire(el, type) { el.dispatchEvent(new Event(type, {bubbles: true, composed: true})); }
  window.__ag = {
    v: 4,
    exists(sels) { return !!q(sels, false); },
    visible(sels) { return !!q(sels, true); },
    text(sels) { return text(q(sels, false)); },
    href(sels) {
      const el = q(sels, false);
      if (!el) return "";
      return el.href || el.getAttribute("href") || text(el);
    },
    attr(sels, name) { const el = q(sels, false); return el ? el.getAttribute(name) : null; },
    click(sels) {
      const el = q(sels, true);
      if (!el || !enabled(el)) return false;
      el.scrollIntoView({block: "center", inline: "center"});
      el.click();
      return true;
    },
    rect(sels) {
      const el = q(sels, true);
      if (!el) return null;
      el.scrollIntoView({block: "center", inline: "center"});
      const r = el.getBoundingClientRect();
      return {x: r.left + r.width / 2, y: r.top + r.height / 2, w: r.width, h: r.height,
              vw: window.innerWidth, vh: window.innerHeight};
    },
    checked(sels) {
      const el = q(sels, false);
      if (!el) return null;
      return el.hasAttribute("checked") || el.getAttribute("aria-checked") === "true" ||
             el.classList.contains("checked") || el.checked === true;
    },
    setText(sels, value) {
      const el = q(sels, true);
      if (!el) return null;
      el.scrollIntoView({block: "center"});
      el.focus();
      const editable = el.isContentEditable;
      if (editable) {
        const range = document.createRange();
        range.selectNodeContents(el);
        const sel = window.getSelection();
        sel.removeAllRanges();
        sel.addRange(range);
        // execCommand: real input events, so Studio's own model of the field updates
        // line by line with real line breaks: insertText("a\nb") is not a line break in every
        // editable, insertLineBreak is
        let ok = true;
        if (el.textContent.length) ok = document.execCommand("delete", false) && ok;
        const lines = value.split("\n");
        lines.forEach((line, i) => {
          if (i > 0) ok = document.execCommand("insertLineBreak", false) && ok;
          if (line) ok = document.execCommand("insertText", false, line) && ok;
        });
        if (!ok) { el.innerText = value; fire(el, "input"); }
      } else if ("value" in el) {
        el.select && el.select();
        if (!document.execCommand("insertText", false, value)) { el.value = value; fire(el, "input"); }
        fire(el, "change");
      } else {
        el.textContent = value; fire(el, "input");
      }
      fire(el, "change");
      el.blur && el.blur();
      return editable ? (el.innerText || "") : (el.value !== undefined ? el.value : text(el));
    },
    // click the item (within items matched by itemSels) whose label text equals one of texts
    // (case-insensitive, trimmed); returns the matched text, or null
    clickItemByText(itemSels, labelSels, texts, checkboxSels) {
      const want = texts.map(t => t.trim().toLowerCase());
      for (const s of itemSels) {
        for (const item of all(s)) {
          if (!visible(item)) continue;
          let lab = null;
          for (const ls of labelSels || []) { lab = item.querySelector(ls); if (lab) break; }
          const t = text(lab || item).toLowerCase();
          const hit = want.find(w => t === w || (w.length > 3 && t.startsWith(w + "\n")));
          if (hit === undefined) continue;
          let target = item;
          for (const cs of checkboxSels || []) { const c = item.matches(cs) ? item : item.querySelector(cs); if (c) { target = c; break; } }
          target.scrollIntoView({block: "center"});
          target.click();
          return text(lab || item);
        }
      }
      return null;
    },
    itemChecked(itemSels, labelSels, text_, checkboxSels) {
      const want = text_.trim().toLowerCase();
      for (const s of itemSels) {
        for (const item of all(s)) {
          let lab = null;
          for (const ls of labelSels || []) { lab = item.querySelector(ls); if (lab) break; }
          if (text(lab || item).toLowerCase() !== want) continue;
          let box = item;
          for (const cs of checkboxSels || []) { const c = item.matches(cs) ? item : item.querySelector(cs); if (c) { box = c; break; } }
          return box.hasAttribute("checked") || box.getAttribute("aria-checked") === "true" || box.checked === true;
        }
      }
      return null;
    },
    itemTexts(itemSels, labelSels) {
      const out = [];
      for (const s of itemSels) {
        for (const item of all(s)) {
          let lab = null;
          for (const ls of labelSels || []) { lab = item.querySelector(ls); if (lab) break; }
          const t = text(lab || item);
          if (t && !out.includes(t)) out.push(t);
        }
        if (out.length) break;
      }
      return out;
    },
    host() { return location.host; },
    url() { return location.href; },
  };
})();
