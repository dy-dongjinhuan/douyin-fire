/* =========================================================
   douyin-fire · uikit.js  (共享交互层)
   侧边栏 / 自定义 Select / 自定义 DatePicker / 涟漪 / 入场 / toast / modal
   依赖：无。所有组件为渐进增强，原生控件可随时回退。
   ========================================================= */
(function () {
  "use strict";
  const UI = {};
  UI.reduceMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* ---------- 图标（内联 SVG 字符串） ---------- */
  const ICONS = {
    caret: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"><path d="M6 9l6 6 6-6"/></svg>',
    check: '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2.4"><path d="M20 6L9 17l-5-5"/></svg>',
    search: '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/></svg>',
    chevL: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"><path d="M15 18l-6-6 6-6"/></svg>',
    chevR: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 18l6-6-6-6"/></svg>',
    close: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 6L6 18M6 6l12 12"/></svg>',
    menu: '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 6h18M3 12h18M3 18h18"/></svg>',
    spark: '<svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor"><path d="M12 2l2.2 6.6L21 11l-6.8 2.4L12 20l-2.2-6.6L3 11l6.8-2.4z"/></svg>'
  };
  UI.ICONS = ICONS;

  /* ---------- 涟漪 ---------- */
  document.addEventListener("click", function (e) {
    const btn = e.target.closest(".btn");
    if (!btn || UI.reduceMotion) return;
    const r = btn.getBoundingClientRect();
    const size = Math.max(r.width, r.height);
    const span = document.createElement("span");
    span.className = "ripple";
    span.style.width = span.style.height = size + "px";
    span.style.left = (e.clientX - r.left - size / 2) + "px";
    span.style.top = (e.clientY - r.top - size / 2) + "px";
    btn.appendChild(span);
    setTimeout(function () { span.remove(); }, 620);
  });

  /* ---------- 侧边栏收起 ---------- */
  UI.initSidebar = function () {
    const sb = document.querySelector(".sidebar");
    if (!sb) return;
    const key = "df_sidebar_collapsed";
    if (localStorage.getItem(key) === "1") sb.classList.add("collapsed");
    document.querySelectorAll("[data-sidebar-toggle]").forEach(function (b) {
      b.addEventListener("click", function () {
        sb.classList.toggle("collapsed");
        localStorage.setItem(key, sb.classList.contains("collapsed") ? "1" : "0");
        document.querySelectorAll(".sidebar-backdrop").forEach(function (bd) {
          bd.classList.toggle("show", !sb.classList.contains("collapsed"));
        });
      });
    });
    document.querySelectorAll(".sidebar-backdrop").forEach(function (bd) {
      bd.addEventListener("click", function () {
        sb.classList.add("collapsed");
        bd.classList.remove("show");
        localStorage.setItem(key, "1");
      });
    });
  };

  /* ---------- 滚动入场 ---------- */
  /* 渐进增强：内容默认可见，JS 就绪后才加 will-animate 进入隐藏态，再逐块揭示。
     任何一步失败（JS 未加载 / IO 不可用 / 报错）内容都保持可见，绝不空白。 */
  UI.observeReveal = function (root) {
    const els = (root || document).querySelectorAll(".reveal");
    if (!("IntersectionObserver" in window) || UI.reduceMotion) {
      els.forEach(function (el) { el.classList.add("in"); });
      return;
    }
    els.forEach(function (el) { el.classList.add("will-animate"); });
    const io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (en.isIntersecting) { en.target.classList.add("in"); io.unobserve(en.target); }
      });
    }, { threshold: 0.12, rootMargin: "0px 0px -40px 0px" });
    els.forEach(function (el) { io.observe(el); });
    // 保险：700ms 后仍在视口内但未揭示的元素强制显示，防止 IO 回调丢失
    setTimeout(function () {
      els.forEach(function (el) {
        if (!el.classList.contains("in")) {
          const r = el.getBoundingClientRect();
          if (r.top < window.innerHeight && r.bottom > 0) el.classList.add("in");
        }
      });
    }, 700);
  };

  /* ---------- 自定义 Select ---------- */
  function Select(el, opts) {
    this.el = el;
    this.opts = Object.assign({
      options: [], value: null, placeholder: "请选择", searchable: true,
      multiple: false, onChange: null, icon: false
    }, opts || {});
    this.value = this.opts.value;
    this.build();
  }
  Select.prototype.build = function () {
    const self = this, o = this.opts;
    this.el.classList.add("select");
    this.el.innerHTML =
      '<button type="button" class="select-trigger">' +
        '<span class="st-value' + (o.value ? "" : " placeholder") + '">' + (o.value ? this.labelOf(o.value) : o.placeholder) + '</span>' +
        '<span class="st-caret">' + ICONS.caret + '</span>' +
      '</button>' +
      '<div class="select-pop">' +
        (o.searchable ? '<div class="select-search-wrap" style="position:relative;padding:2px 2px 6px"><span style="position:absolute;left:12px;top:11px;color:var(--text-4);pointer-events:none">' + ICONS.search + '</span><input class="select-search" style="padding-left:32px" placeholder="搜索…"></div>' : '') +
        '<div class="select-opts"></div>' +
      '</div>';
    this.trigger = this.el.querySelector(".select-trigger");
    this.pop = this.el.querySelector(".select-pop");
    this.valEl = this.el.querySelector(".st-value");
    this.optsBox = this.el.querySelector(".select-opts");
    this.renderOpts("");
    const self2 = this;
    this.trigger.addEventListener("click", function (e) { e.stopPropagation(); self2.toggle(); });
    if (o.searchable) {
      const s = this.el.querySelector(".select-search");
      s.addEventListener("click", function (e) { e.stopPropagation(); });
      s.addEventListener("input", function () { self2.renderOpts(s.value.trim().toLowerCase()); });
    }
    document.addEventListener("click", function (e) {
      if (!self2.el.contains(e.target)) self2.close();
    });
    // 键盘
    this.trigger.addEventListener("keydown", function (e) {
      if (e.key === "ArrowDown" || e.key === "Enter") { e.preventDefault(); self2.open(); }
    });
  };
  Select.prototype.labelOf = function (v) {
    const f = this.opts.options.find(function (x) { return x.value === v; });
    return f ? f.label : v;
  };
  Select.prototype.renderOpts = function (q) {
    const self = this, o = this.opts;
    this.optsBox.innerHTML = "";
    const list = o.options.filter(function (x) { return !q || x.label.toLowerCase().indexOf(q) >= 0; });
    if (!list.length) {
      const empty = document.createElement("div");
      empty.style.cssText = "padding:10px;color:var(--text-4);font-size:13px;text-align:center";
      empty.textContent = "无匹配项";
      this.optsBox.appendChild(empty); return;
    }
    list.forEach(function (op) {
      const d = document.createElement("div");
      d.className = "select-opt" + (op.value === self.value ? " sel" : "");
      d.innerHTML = (o.icon && op.icon ? '<span class="opt-ico">' + op.icon + '</span>' : '') +
        '<span class="opt-label">' + op.label + '</span>' +
        '<span class="check">' + ICONS.check + '</span>';
      d.addEventListener("click", function (e) { e.stopPropagation(); self.choose(op.value); });
      self.optsBox.appendChild(d);
    });
  };
  Select.prototype.choose = function (v) {
    this.value = v;
    this.valEl.textContent = this.labelOf(v);
    this.valEl.classList.remove("placeholder");
    this.renderOpts(this.opts.searchable ? (this.el.querySelector(".select-search").value || "").trim().toLowerCase() : "");
    if (this.opts.onChange) this.opts.onChange(v);
    if (!this.opts.multiple) this.close();
  };
  Select.prototype.open = function () { this.pop.classList.add("open"); this.trigger.classList.add("open"); if (this.opts.searchable) { const s = this.el.querySelector(".select-search"); if (s) s.focus(); } };
  Select.prototype.close = function () { this.pop.classList.remove("open"); this.trigger.classList.remove("open"); };
  Select.prototype.toggle = function () { this.pop.classList.contains("open") ? this.close() : this.open(); };
  UI.Select = Select;

  /* ---------- 自定义 DatePicker ---------- */
  function DatePicker(el, opts) {
    this.el = el;
    this.opts = Object.assign({ value: null, placeholder: "选择日期", format: "YYYY-MM-DD", onChange: null }, opts || {});
    this.value = this.opts.value;
    this.view = this.value ? new Date(this.value) : new Date();
    this.build();
  }
  function pad(n) { return n < 10 ? "0" + n : "" + n; }
  function fmt(d) { return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate()); }
  function sameDay(a, b) { return a && b && a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate(); }
  DatePicker.prototype.build = function () {
    const self = this, o = this.opts;
    this.el.classList.add("datepicker");
    this.el.innerHTML =
      '<button type="button" class="dp-trigger">' +
        '<span class="dp-value' + (o.value ? "" : " placeholder") + '">' + (o.value ? fmt(o.value) : o.placeholder) + '</span>' +
        '<span class="st-caret" style="margin-left:auto;color:var(--text-3)">' + ICONS.caret + '</span>' +
      '</button>' +
      '<div class="dp-pop">' +
        '<div class="dp-head"><div class="dp-title"></div><div class="dp-nav">' +
          '<button type="button" class="dp-prev">' + ICONS.chevL + '</button>' +
          '<button type="button" class="dp-next">' + ICONS.chevR + '</button>' +
        '</div></div>' +
        '<div class="dp-grid dp-dow-row"></div>' +
        '<div class="dp-grid dp-days"></div>' +
        '<div class="dp-foot"><button type="button" class="btn ghost sm dp-today">今天</button><button type="button" class="btn sm dp-clear">清除</button></div>' +
      '</div>';
    this.trigger = this.el.querySelector(".dp-trigger");
    this.pop = this.el.querySelector(".dp-pop");
    this.titleEl = this.el.querySelector(".dp-title");
    this.daysEl = this.el.querySelector(".dp-days");
    this.dowRow = this.el.querySelector(".dp-dow-row");
    const dows = ["日", "一", "二", "三", "四", "五", "六"];
    dows.forEach(function (d) { const s = document.createElement("div"); s.className = "dp-dow"; s.textContent = d; self.dowRow.appendChild(s); });
    this.render();
    const self2 = this;
    this.trigger.addEventListener("click", function (e) { e.stopPropagation(); self2.toggle(); });
    this.pop.querySelector(".dp-prev").addEventListener("click", function (e) { e.stopPropagation(); self2.view.setMonth(self2.view.getMonth() - 1); self2.render(); });
    this.pop.querySelector(".dp-next").addEventListener("click", function (e) { e.stopPropagation(); self2.view.setMonth(self2.view.getMonth() + 1); self2.render(); });
    this.pop.querySelector(".dp-today").addEventListener("click", function (e) { e.stopPropagation(); self2.view = new Date(); self2.render(); });
    this.pop.querySelector(".dp-clear").addEventListener("click", function (e) { e.stopPropagation(); self2.set(null); self2.close(); });
    this.pop.addEventListener("click", function (e) { e.stopPropagation(); });
    document.addEventListener("click", function (e) { if (!self2.el.contains(e.target)) self2.close(); });
  };
  DatePicker.prototype.render = function () {
    const self = this;
    this.titleEl.textContent = this.view.getFullYear() + " 年 " + (this.view.getMonth() + 1) + " 月";
    this.daysEl.innerHTML = "";
    const y = this.view.getFullYear(), m = this.view.getMonth();
    const first = new Date(y, m, 1).getDay();
    const days = new Date(y, m + 1, 0).getDate();
    const prevDays = new Date(y, m, 0).getDate();
    const today = new Date();
    for (let i = 0; i < first; i++) {
      const d = document.createElement("div"); d.className = "dp-day muted"; d.textContent = prevDays - first + 1 + i; this.daysEl.appendChild(d);
    }
    for (let i = 1; i <= days; i++) {
      const d = new Date(y, m, i);
      const cell = document.createElement("div");
      cell.className = "dp-day"; cell.textContent = i;
      if (sameDay(d, today)) cell.classList.add("today");
      if (sameDay(d, this.value)) cell.classList.add("sel");
      (function (date) {
        cell.addEventListener("click", function () { self.set(date); self.close(); });
      })(d);
      this.daysEl.appendChild(cell);
    }
    const total = first + days;
    const tail = (7 - (total % 7)) % 7;
    for (let i = 1; i <= tail; i++) {
      const d = document.createElement("div"); d.className = "dp-day muted"; d.textContent = i; this.daysEl.appendChild(d);
    }
  };
  DatePicker.prototype.set = function (date) {
    this.value = date;
    this.el.querySelector(".dp-value").textContent = date ? fmt(date) : this.opts.placeholder;
    this.el.querySelector(".dp-value").classList.toggle("placeholder", !date);
    this.render();
    if (this.opts.onChange) this.opts.onChange(date ? fmt(date) : null);
  };
  DatePicker.prototype.open = function () { this.pop.classList.add("open"); this.trigger.classList.add("open"); };
  DatePicker.prototype.close = function () { this.pop.classList.remove("open"); this.trigger.classList.remove("open"); };
  DatePicker.prototype.toggle = function () { this.pop.classList.contains("open") ? this.close() : this.open(); };
  UI.DatePicker = DatePicker;

  /* ---------- Toast ---------- */
  UI.toast = function (msg, type, ms) {
    type = type || "info"; ms = ms || 2600;
    let wrap = document.querySelector(".toast-wrap");
    if (!wrap) { wrap = document.createElement("div"); wrap.className = "toast-wrap"; document.body.appendChild(wrap); }
    const t = document.createElement("div");
    t.className = "toast " + type;
    const ic = type === "success" ? ICONS.check : type === "error" ? ICONS.close : ICONS.spark;
    t.innerHTML = '<span class="t-ico">' + ic + '</span><span>' + msg + '</span>';
    wrap.appendChild(t);
    requestAnimationFrame(function () { t.classList.add("show"); });
    setTimeout(function () { t.classList.remove("show"); setTimeout(function () { t.remove(); }, 300); }, ms);
  };

  /* ---------- Modal ---------- */
  UI.modal = function (opts) {
    opts = opts || {};
    let mask = document.querySelector(".modal-mask");
    if (!mask) { mask = document.createElement("div"); mask.className = "modal-mask"; document.body.appendChild(mask); }
    const ic = opts.icon || ICONS.spark;
    mask.innerHTML =
      '<div class="modal-card">' +
        (opts.icon !== false ? '<div class="modal-icon">' + ic + '</div>' : '') +
        '<h3>' + (opts.title || "") + '</h3>' +
        '<p>' + (opts.body || "") + '</p>' +
        '<div class="modal-actions">' + (opts.cancelText ? '<button class="btn ghost" data-m="cancel">' + opts.cancelText + '</button>' : '') +
        '<button class="btn ' + (opts.confirmClass || "primary") + '" data-m="ok">' + (opts.okText || "确定") + '</button></div>' +
      '</div>';
    mask.classList.add("open");
    return new Promise(function (resolve) {
      mask.querySelector('[data-m="ok"]').addEventListener("click", function () { UI.closeModal(); resolve(true); });
      const c = mask.querySelector('[data-m="cancel"]');
      if (c) c.addEventListener("click", function () { UI.closeModal(); resolve(false); });
      mask.addEventListener("click", function (e) { if (e.target === mask) { UI.closeModal(); resolve(false); } });
    });
  };
  UI.closeModal = function () { const m = document.querySelector(".modal-mask"); if (m) m.classList.remove("open"); };

  /* ---------- fetch 封装 ---------- */
  UI.fetchJSON = function (url, opts) {
    opts = opts || {};
    return fetch(url, Object.assign({ headers: { "Content-Type": "application/json" }, credentials: "same-origin" }, opts))
      .then(function (r) { return r.json().catch(function () { return {}; }).then(function (d) { return { ok: r.ok, status: r.status, data: d }; }); });
  };

  /* ---------- 自动初始化 ---------- */
  document.addEventListener("DOMContentLoaded", function () {
    UI.initSidebar();
    UI.observeReveal();
  });

  window.UI = UI;
})();
