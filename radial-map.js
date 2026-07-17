/* LiveMTG マップ描画エンジン（依存ライブラリなし・決定論）
 *
 * mermaid mindmapの置き換え（2026-07-16 依頼者決定）。レイアウト2種を内蔵し、
 * 右上のトグルで切替できる（選択はlocalStorageに記憶・展開状態は両者で共有）：
 *  - wings（既定）: 左右二翼のマインドマップ。トピックを左右に振り分け、全て水平に読む。
 *    どれだけ増えても縦に伸びるだけで、距離は固定・破綻しない（放射環方式は
 *    混むと半径が爆発して「アメンボ」化するため廃止。2026-07-16 試行錯誤の結論）
 *  - cards: ネストカード・グリッド。線を使わずトピック=色カード、グループ=行、
 *    項目=行内に差し込み。密度と一覧性が最も高い
 * 共通原則: 段階表示（項目は「+N」→クリック展開）・箱は不透明（半透明は線が透ける）・
 * トピックごとの枝色。
 *
 * 使い方:
 *   LiveMTGRadial.render(container, model, opts)
 *     model = {title, topics:[{topic, groups:[{label, items:["…"|{label}]}]}]}
 *     opts  = {layout:'wings'|'cards', layoutKey:'livemtg_radial_layout',
 *              expandAll:false, expanded:Set<string>, interactive:true, onToggle(key, open)}
 *   戻り値 {width, height, element}
 * 展開キーは "トピック␟グループ"。
 */
(function () {
  "use strict";

  var PALETTE = ["#5b8def", "#41a58d", "#8f7ee6", "#d9903f", "#c96f8a", "#4fa3c9", "#8aa353", "#b5825a"];
  function rgba(hex, a) {
    var n = parseInt(hex.slice(1), 16);
    return "rgba(" + (n >> 16) + "," + ((n >> 8) & 255) + "," + (n & 255) + "," + a + ")";
  }
  // 白と混ぜた「不透明の淡色」。rgbaの半透明だと下を通る枝線が透ける
  function tint(hex, t) {
    var n = parseInt(hex.slice(1), 16), r = n >> 16, g = (n >> 8) & 255, b = n & 255;
    var mix = function (c) { return Math.round(255 - (255 - c) * t); };
    return "rgb(" + mix(r) + "," + mix(g) + "," + mix(b) + ")";
  }

  var CSS = [
    ".rdwrap{display:flex;flex-direction:column;align-items:center;gap:10px;width:max-content;",
    "  font-family:-apple-system,'SF Pro Text','Helvetica Neue','Hiragino Kaku Gothic ProN','Yu Gothic',sans-serif}",
    ".rdbar{display:flex;gap:4px;background:#e0e3e9;border-radius:10px;padding:3px;position:sticky;top:6px;z-index:3;align-self:center}",
    ".rdbar button{font-family:inherit;font-size:12px;font-weight:800;border:none;border-radius:8px;padding:5px 14px;",
    "  cursor:pointer;background:transparent;color:#6e6e73}",
    ".rdbar button.on{background:#fff;color:#0066cc;box-shadow:0 1px 3px rgba(0,0,0,.12)}",
    ".rdmap{position:relative}",
    ".rdmap svg{position:absolute;inset:0;pointer-events:none}",
    ".rdmap svg path{fill:none;stroke-linecap:round}",
    ".rd-node{position:absolute;transform:translate(-50%,-50%);box-sizing:border-box;width:max-content;max-width:200px;",
    "  padding:7px 11px;border-radius:10px;border:1px solid #d2d2d7;background:#fff;color:#1d1d1f;",
    "  font-size:12.5px;line-height:1.45;font-weight:600;text-align:center;overflow-wrap:anywhere;",
    "  display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:2;overflow:hidden}",
    ".rd-node.rd-root{background:#1d1d1f;color:#fff;border-color:#1d1d1f;font-size:14px;font-weight:800;",
    "  max-width:220px;padding:12px 16px;border-radius:14px;-webkit-line-clamp:3}",
    ".rd-node.rd-topic{font-weight:800;font-size:13px}",
    ".rd-node.rd-item{color:#3a3a3c;text-align:left}",
    ".rd-node.rd-clickable,.rd-crow.rd-clickable{cursor:pointer}",
    ".rd-badge{display:inline-block;margin-left:6px;padding:0 7px;border-radius:999px;background:#0071e3;",
    "  color:#fff;font-size:10.5px;font-weight:800;vertical-align:1px;flex:none}",
    /* cards */
    ".rd-cards{display:grid;gap:14px;padding:2px}",
    ".rd-card{box-sizing:border-box;width:300px;border:1.5px solid #e4e4e8;border-radius:14px;background:#fff;",
    "  overflow:hidden;box-shadow:0 1px 6px rgba(0,0,0,.05);align-self:start}",
    ".rd-card h4{margin:0;padding:10px 14px;color:#fff;font-size:13.5px;font-weight:800;letter-spacing:.01em}",
    ".rd-crow{padding:9px 14px;border-top:1px solid #ececf0;font-size:12.5px;font-weight:700;color:#1d1d1f;",
    "  display:flex;justify-content:space-between;align-items:center;gap:8px;background:#fff;text-align:left}",
    ".rd-cits{padding:2px 14px 9px 24px;background:#fff}",
    ".rd-cits div{font-size:12px;color:#3a3a3c;padding:4px 0 4px 10px;border-left:3px solid #d2d2d7;margin:4px 0;",
    "  line-height:1.55;font-weight:600;overflow-wrap:anywhere}"
  ].join("\n");

  function ensureCss(doc) {
    if (!doc.getElementById("rdmap-style")) {
      var st = doc.createElement("style");
      st.id = "rdmap-style";
      st.textContent = CSS;
      doc.head.appendChild(st);
    }
  }

  function text(v) {
    if (v == null) return "";
    if (typeof v === "object") return String(v.label || v.detail || v.what || "").trim();
    return String(v).trim();
  }

  function normTopics(model) {
    var out = [];
    (model.topics || []).forEach(function (t, i) {
      var label = text(t.topic);
      if (!label) return;
      var groups = [];
      (t.groups || []).forEach(function (g) {
        var gl = text(g.label);
        if (!gl) return;
        groups.push({ label: gl, key: label + "␟" + gl, items: (g.items || []).map(text).filter(Boolean) });
      });
      if (groups.length) out.push({ label: label, color: PALETTE[i % PALETTE.length], groups: groups });
    });
    return out;
  }

  /* ===== 左右二翼レイアウト ===== */
  function buildWings(doc, host, model, expanded, opts) {
    var topics = normTopics(model);
    var TDX = 245, GDX = 245, IDX = 255;      // 根→トピック→グループ→項目の水平距離（固定）
    var GSTEP = 58, ISTEP = 54, TPAD = 34;
    var isOpen = function (g) { return !!opts.expandAll || expanded.has(g.key); };
    var slotH = function (g) { return Math.max(GSTEP, isOpen(g) ? g.items.length * ISTEP : GSTEP); };

    var sides = { R: [], L: [] };
    topics.forEach(function (t, i) { (i % 2 ? sides.L : sides.R).push(t); });
    var layoutSide = function (list) {
      var hts = list.map(function (t) {
        var h = 0; t.groups.forEach(function (g) { h += slotH(g); });
        return Math.max(h, 80) + TPAD;
      });
      var total = 0; hts.forEach(function (v) { total += v; });
      return { hts: hts, total: total };
    };
    var right = layoutSide(sides.R), left = layoutSide(sides.L);

    var nodes = [], links = [];
    var rootN = { cls: "rd-root", label: text(model.title) || "会議", x: 0, y: 0 };
    nodes.push(rootN);
    [[1, sides.R, right], [-1, sides.L, left]].forEach(function (cfg) {
      var sign = cfg[0], list = cfg[1], lay = cfg[2];
      var cy = -lay.total / 2;
      list.forEach(function (t, k) {
        var ty = cy + lay.hts[k] / 2, tx = sign * TDX;
        cy += lay.hts[k];
        nodes.push({ cls: "rd-topic", label: t.label, x: tx, y: ty, color: t.color });
        links.push({ x1: sign * 105, y1: 0, x2: tx - sign * 92, y2: ty, color: t.color, w: 4.5, a: 0.75 });
        var gh = t.groups.map(slotH), gtotal = 0;
        gh.forEach(function (v) { gtotal += v; });
        var gy = ty - gtotal / 2;
        t.groups.forEach(function (g, gi) {
          var gyy = gy + gh[gi] / 2, gx = tx + sign * GDX;
          gy += gh[gi];
          var open = isOpen(g);
          nodes.push({ cls: "rd-group", label: g.label, x: gx, y: gyy, color: t.color,
                       key: g.key, count: g.items.length, open: open });
          links.push({ x1: tx + sign * 92, y1: ty, x2: gx - sign * 104, y2: gyy, color: t.color, w: 2.8, a: 0.55 });
          if (open) g.items.forEach(function (itm, k2) {
            var iy = gyy + (k2 - (g.items.length - 1) / 2) * ISTEP, ix = gx + sign * IDX;
            nodes.push({ cls: "rd-item", label: itm, x: ix, y: iy, color: t.color });
            links.push({ x1: gx + sign * 104, y1: gyy, x2: ix - sign * 108, y2: iy, color: t.color, w: 1.8, a: 0.5 });
          });
        });
      });
    });

    // バウンディングボックス→キャンバス化
    var minX = 1e9, maxX = -1e9, minY = 1e9, maxY = -1e9;
    nodes.forEach(function (n) {
      minX = Math.min(minX, n.x); maxX = Math.max(maxX, n.x);
      minY = Math.min(minY, n.y); maxY = Math.max(maxY, n.y);
    });
    var PADX = 130, PADY = 60;
    var w = Math.ceil(maxX - minX + PADX * 2), h = Math.ceil(maxY - minY + PADY * 2);
    var ox = PADX - minX, oy = PADY - minY;

    var el = doc.createElement("div");
    el.className = "rdmap";
    el.style.width = w + "px";
    el.style.height = h + "px";
    var svgNs = "http://www.w3.org/2000/svg";
    var svg = doc.createElementNS(svgNs, "svg");
    svg.setAttribute("width", w); svg.setAttribute("height", h);
    el.appendChild(svg);
    links.forEach(function (l) {
      var x1 = l.x1 + ox, y1 = l.y1 + oy, x2 = l.x2 + ox, y2 = l.y2 + oy;
      var c1x = x1 + (x2 - x1) * 0.45, c2x = x1 + (x2 - x1) * 0.85;
      var path = doc.createElementNS(svgNs, "path");
      path.setAttribute("d", "M" + x1 + " " + y1 + " C" + c1x + " " + (y1 + (y2 - y1) * 0.1) + " " + c2x + " " + y2 + " " + x2 + " " + y2);
      path.setAttribute("stroke", rgba(l.color || "#b9bec6", l.a));
      path.setAttribute("stroke-width", l.w);
      svg.appendChild(path);
    });
    nodes.forEach(function (n) {
      var box = doc.createElement("div");
      box.className = "rd-node " + n.cls;
      box.style.left = (n.x + ox) + "px";
      box.style.top = (n.y + oy) + "px";
      box.textContent = n.label;
      if (n.color) {
        if (n.cls === "rd-topic") { box.style.background = n.color; box.style.borderColor = n.color; box.style.color = "#fff"; }
        else if (n.cls === "rd-group") { box.style.background = tint(n.color, 0.13); box.style.borderColor = tint(n.color, 0.55); }
        else if (n.cls === "rd-item") { box.style.borderColor = tint(n.color, 0.5); }
      }
      if (n.cls === "rd-group" && n.count > 0) attachBadge(doc, box, n, expanded, opts);
      el.appendChild(box);
    });
    host.appendChild(el);
  }

  /* ===== ネストカード・グリッド ===== */
  function buildCards(doc, host, model, expanded, opts) {
    var topics = normTopics(model);
    var cols = Math.min(4, Math.max(1, Math.ceil(topics.length / 2)));
    var grid = doc.createElement("div");
    grid.className = "rd-cards";
    grid.style.gridTemplateColumns = "repeat(" + cols + ", 300px)";
    topics.forEach(function (t) {
      var card = doc.createElement("div");
      card.className = "rd-card";
      var head = doc.createElement("h4");
      head.textContent = t.label;
      head.style.background = t.color;
      card.appendChild(head);
      t.groups.forEach(function (g) {
        var open = !!opts.expandAll || expanded.has(g.key);
        var row = doc.createElement("div");
        row.className = "rd-crow";
        var sp = doc.createElement("span");
        sp.textContent = g.label;
        row.appendChild(sp);
        var n = { key: g.key, count: g.items.length, open: open, color: t.color };
        if (g.items.length) attachBadge(doc, row, n, expanded, opts);
        card.appendChild(row);
        if (open && g.items.length) {
          var its = doc.createElement("div");
          its.className = "rd-cits";
          g.items.forEach(function (x) {
            var dv = doc.createElement("div");
            dv.style.borderLeftColor = tint(t.color, 0.6);
            dv.textContent = x;
            its.appendChild(dv);
          });
          card.appendChild(its);
        }
      });
      grid.appendChild(card);
    });
    host.appendChild(grid);
  }

  // 「+N / −」バッジと開閉クリック（両レイアウト共通）
  function attachBadge(doc, box, n, expanded, opts) {
    var badge = doc.createElement("span");
    badge.className = "rd-badge";
    badge.textContent = n.open ? "−" : "+" + n.count;
    if (n.color) badge.style.background = n.open ? "#86868b" : n.color;
    box.appendChild(badge);
    if (opts.interactive === false) { if (n.open) badge.remove(); return; }
    box.classList.add("rd-clickable");
    box.title = n.open ? "クリックで項目を畳む" : "クリックで項目を開く";
    box.addEventListener("click", function (e) {
      e.stopPropagation();
      if (n.open) expanded.delete(n.key); else expanded.add(n.key);
      if (opts.onToggle) opts.onToggle(n.key, !n.open);
      render(opts._container, opts._model, opts);
    });
  }

  function getLayout(opts) {
    if (opts.switchable === false) return opts.layout === "cards" ? "cards" : "wings";
    var key = opts.layoutKey || "livemtg_radial_layout";
    try {
      var v = window.localStorage.getItem(key);
      if (v === "wings" || v === "cards") return v;
    } catch (e) { /* file://等でlocalStorage不可なら既定へ */ }
    return opts.layout === "cards" ? "cards" : "wings";
  }

  function render(container, model, opts) {
    opts = opts || {};
    opts._container = container; opts._model = model;
    var doc = container.ownerDocument;
    ensureCss(doc);
    var expanded = opts.expanded || (opts.expanded = new Set());
    var layout = getLayout(opts);

    var wrap = doc.createElement("div");
    wrap.className = "rdwrap";
    if (opts.interactive !== false && opts.switchable !== false) {
      var bar = doc.createElement("div");
      bar.className = "rdbar";
      [["wings", "マップ"], ["cards", "カード"]].forEach(function (def) {
        var b = doc.createElement("button");
        b.type = "button";
        b.textContent = def[1];
        if (def[0] === layout) b.className = "on";
        b.addEventListener("click", function (e) {
          e.stopPropagation();
          try { window.localStorage.setItem(opts.layoutKey || "livemtg_radial_layout", def[0]); } catch (err) {}
          opts.layout = def[0];
          render(container, model, opts);
        });
        bar.appendChild(b);
      });
      wrap.appendChild(bar);
    }
    if (layout === "cards") buildCards(doc, wrap, model, expanded, opts);
    else buildWings(doc, wrap, model, expanded, opts);

    container.textContent = "";
    container.appendChild(wrap);
    return { width: wrap.offsetWidth || 0, height: wrap.offsetHeight || 0, element: wrap };
  }

  var api = { render: render };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (typeof window !== "undefined") window.LiveMTGRadial = api;
})();
