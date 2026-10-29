/**
 * dcv-graph — the interactive canvas from the two Fabric apps, ported light.
 *
 * Carried across from the two apps:
 * the layout arithmetic (folder columns with subfolder boxes and lane wrapping for
 * TMDL, centred layer bands for M), the barycenter sweeps, the bezier control-point
 * rule, the folder palette, the upstream/downstream highlight, the detail panel,
 * focus mode and search.
 *
 * Deliberately dropped, because a catalogue page is not an app: the metadata and
 * DAX-breakdown views, the paste editor, file drop, the copy buttons, the view
 * tabs, the ticker and the source panel.
 *
 * One behaviour is deliberately DIFFERENT rather than ported. In the apps the
 * canvas owns the viewport, so a plain wheel scrolls it. Here the canvas is a
 * block inside a long document, and swallowing the wheel would trap the page
 * scroll the moment the pointer crossed a graph. So: drag to pan, Ctrl or pinch
 * to zoom, buttons for the rest, and a plain wheel scrolls the page as it should.
 */
(function () {
  'use strict';

  /* ----------------------------------------------------------------- utils */

  function h(tag, props, children) {
    var el = document.createElement(tag);
    if (props) {
      Object.keys(props).forEach(function (k) {
        var v = props[k];
        if (v == null || v === false) return;
        if (k === 'text') el.textContent = v;
        else if (k === 'class') el.className = v;
        else if (k === 'style') Object.assign(el.style, v);
        else if (k.slice(0, 2) === 'on') el.addEventListener(k.slice(2), v);
        else el.setAttribute(k, v === true ? '' : v);
      });
    }
    (children || []).forEach(function (c) {
      if (c == null || c === false) return;
      el.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
    });
    return el;
  }

  function svgEl(tag, attrs) {
    var el = document.createElementNS('http://www.w3.org/2000/svg', tag);
    Object.keys(attrs || {}).forEach(function (k) { el.setAttribute(k, attrs[k]); });
    return el;
  }

  function clear(el) { while (el.firstChild) el.removeChild(el.firstChild); }
  function clamp(v, lo, hi) { return Math.min(hi, Math.max(lo, v)); }

  /* Case-insensitive then case-sensitive. The parsers' rule, so a ported layout
     lands rows in the same order the app puts them in. */
  function compareText(a, b) {
    var la = String(a).toLowerCase(), lb = String(b).toLowerCase();
    if (la < lb) return -1;
    if (la > lb) return 1;
    return a < b ? -1 : a > b ? 1 : 0;
  }

  var UNFOLDERED = 'Other';

  function compareFolders(a, b) {
    if (a === UNFOLDERED) return b === UNFOLDERED ? 0 : 1;
    if (b === UNFOLDERED) return -1;
    return compareText(a, b);
  }

  /* ---------------------------------------------------------------- palette */
  /* Carried verbatim from the apps. Each hue has an explicit dark pair rather
     than an opacity trick: blue-violet reads far less saturated than green at
     the same chroma, and the compensation is baked into these values. */

  var PALETTE = [
    { light: { tint: '#EAF9E4', border: '#C5E6B8', dot: '#49941F', text: '#2E5D14' },
      dark: { tint: '#273C23', border: '#445F3E', dot: '#81C671', text: '#BEE4B5' } },
    { light: { tint: '#DAFCF9', border: '#9DEBE5', dot: '#009E97', text: '#00625E' },
      dark: { tint: '#033E3E', border: '#1B6262', dot: '#00CDCE', text: '#94E9E8' } },
    { light: { tint: '#DFF7FF', border: '#AADFFF', dot: '#007DFB', text: '#0C528C' },
      dark: { tint: '#0B3954', border: '#225C80', dot: '#00BDFF', text: '#98E1FF' } },
    { light: { tint: '#F4EFFF', border: '#DBCEFF', dot: '#8852FD', text: '#524189' },
      dark: { tint: '#322F59', border: '#524F87', dot: '#A79AFF', text: '#CFCEFF' } },
    { light: { tint: '#FFEBFE', border: '#FEC6F0', dot: '#C144AC', text: '#753368' },
      dark: { tint: '#462944', border: '#6E476B', dot: '#E988E2', text: '#FAC3F4' } },
    { light: { tint: '#FFECEA', border: '#FFC8C4', dot: '#D2484D', text: '#833032' },
      dark: { tint: '#4C2932', border: '#754752', dot: '#FB88A5', text: '#FFC2D1' } },
    { light: { tint: '#FFF1DD', border: '#F6D4A7', dot: '#B66B00', text: '#734400' },
      dark: { tint: '#4A2D1E', border: '#734C38', dot: '#F69561', text: '#FFC9AD' } },
    { light: { tint: '#F1F7DF', border: '#D5E1AD', dot: '#778A00', text: '#4B5700' },
      dark: { tint: '#3D3515', border: '#62562C', dot: '#CCAF3B', text: '#E7D89F' } }
  ];

  var NEUTRAL = {
    light: { tint: '#F2F4F6', border: '#D6DBDF', dot: '#878D93', text: '#5D646B' },
    dark: { tint: '#343634', border: '#555854', dot: '#9CA09B', text: '#CED2CD' }
  };

  /* Direction is the one thing allowed a saturated colour, so it never competes
     with folder identity. */
  var DIRECTION = {
    upstream: { light: '#E08A1E', dark: '#E0A93E' },
    downstream: { light: '#58A545', dark: '#73B761' },
    selected: { light: '#34383A', dark: '#C9CDD0' },
    /* Deliberately darker than --color-input: at the token value the chains all
       but vanished against the canvas, and the chains are the point. */
    edge: { light: '#AAB4BD', dark: '#5C6268' }
  };

  function isDark() {
    return document.documentElement.classList.contains('dark') ||
      document.body.classList.contains('dark');
  }

  /* --------------------------------------------------------- TMDL layout */

  var T_NODE_W = 252, T_NODE_H = 58;
  var COLUMN_INSET = 28, ROW_GAP = 20, COLUMN_GAP = 172;
  var COLUMN_PADDING_X = 64, COLUMN_PAD_BOTTOM = 48, COLUMN_TOP = 40;
  var HEADER_INSET = 13, PILL_HEIGHT = 30;
  var SUBFOLDER_HEADER = HEADER_INSET * 2 + PILL_HEIGHT;
  var COLUMN_PADDING_TOP = COLUMN_TOP + SUBFOLDER_HEADER;
  var INNER_GAP = COLUMN_GAP - COLUMN_INSET * 2;
  var MAX_ROWS_PER_LANE = 20, MAX_LANES = 3, PANEL_INSET = 10, MAX_DEPTH = 3;

  function laneCount(n) {
    if (n <= MAX_ROWS_PER_LANE) return n > 0 ? 1 : 0;
    return Math.min(MAX_LANES, Math.ceil(n / MAX_ROWS_PER_LANE));
  }

  function buildGroups(folder, list, maxDepth) {
    var root = { key: folder, label: null, depth: 0, measures: [], children: [] };
    list.forEach(function (m) {
      var node = root;
      (m.folderPath || [folder]).slice(1, maxDepth + 1).forEach(function (seg) {
        var child = node.children.find(function (c) { return c.label === seg; });
        if (!child) {
          child = { key: node.key + ' / ' + seg, label: seg, depth: node.depth + 1,
            measures: [], children: [] };
          node.children.push(child);
        }
        node = child;
      });
      node.measures.push(m);
    });
    (function sortGroup(g) {
      g.measures.sort(function (a, b) { return compareText(a.name, b.name); });
      g.children.sort(function (a, b) { return compareText(a.label, b.label); });
      g.children.forEach(sortGroup);
    })(root);
    return root;
  }

  function flattenGroups(g, out) {
    out.push(g);
    g.children.forEach(function (c) { flattenGroups(c, out); });
    return out;
  }

  function countOf(g) {
    return g.measures.length + g.children.reduce(function (n, c) { return n + countOf(c); }, 0);
  }

  function layoutTmdl(measures, folderOrder) {
    var byName = new Map(measures.map(function (m) { return [m.name, m]; }));
    var columnIndex = new Map(folderOrder.map(function (f, i) { return [f, i]; }));
    var byTop = new Map(folderOrder.map(function (f) { return [f, []]; }));
    measures.forEach(function (m) {
      var list = byTop.get(m.folder) || byTop.get(folderOrder[folderOrder.length - 1]);
      if (list) list.push(m);
    });

    // Deepest nesting anywhere in the model, not per column: a folder with no
    // subfolders still has to start its blocks on the same line as one with two.
    var maxDepth = Math.min(MAX_DEPTH, measures.reduce(function (d, m) {
      return Math.max(d, (m.folderPath || []).length - 1);
    }, 0));
    var blockTop = COLUMN_PADDING_TOP + maxDepth * SUBFOLDER_HEADER;

    var columns = folderOrder.map(function (folder) {
      return { folder: folder, root: buildGroups(folder, byTop.get(folder) || [], maxDepth) };
    });

    var groupsOf = new Map();
    columns.forEach(function (col) { groupsOf.set(col.folder, flattenGroups(col.root, [])); });

    var rowOf = new Map(), bandOf = new Map();
    columns.forEach(function (col) {
      var r = 0;
      groupsOf.get(col.folder).forEach(function (g, band) {
        bandOf.set(g.key, band);
        g.measures.forEach(function (m) { rowOf.set(m.id, r++); });
      });
    });

    function sweep(forward) {
      var idx = columns.map(function (_, i) { return i; });
      (forward ? idx.slice(1) : idx.slice(0, -1).reverse()).forEach(function (colIdx) {
        var col = columns[colIdx];
        var start = 0;
        groupsOf.get(col.folder).forEach(function (g) {
          var groupStart = start;
          start += g.measures.length;
          if (g.measures.length < 2) return;
          var scored = g.measures.map(function (m) {
            var rows = (forward ? m.dependsOn : m.usedBy)
              .map(function (n) { return byName.get(n); })
              .filter(function (n) { return !!n && columnIndex.get(n.folder) !== colIdx; })
              .map(function (n) { return rowOf.get(n.id); })
              .filter(function (r) { return r !== undefined; });
            var score = rows.length
              ? rows.reduce(function (a, b) { return a + b; }, 0) / rows.length
              : rowOf.get(m.id);
            return { m: m, score: score };
          });
          scored.sort(function (a, b) { return a.score - b.score || compareText(a.m.name, b.m.name); });
          g.measures = scored.map(function (s) { return s.m; });
          g.measures.forEach(function (m, i) { rowOf.set(m.id, groupStart + i); });
        });
      });
    }
    for (var pass = 0; pass < 3; pass++) { sweep(true); sweep(false); }

    var nodes = {}, columnBounds = [], panelBounds = [], maxHeight = 0;
    // Columns are not on a fixed pitch: a folder that wraps into lanes is wider
    // than one that does not, so each column starts where the last one ended.
    var x = COLUMN_PADDING_X;

    columns.forEach(function (col) {
      var lane = { next: 0 };

      function placeGroup(g, left) {
        var cursor = left, bottom = blockTop, top = blockTop, placed = false;
        var n = g.measures.length;
        if (n > 0) {
          var lanes = laneCount(n);
          var perLane = Math.ceil(n / lanes);
          var base = lane.next;
          g.measures.forEach(function (m, i) {
            var inGroup = Math.floor(i / perLane);
            nodes[m.id] = {
              id: m.id, band: bandOf.get(g.key),
              x: cursor + inGroup * (T_NODE_W + INNER_GAP),
              y: blockTop + (i % perLane) * (T_NODE_H + ROW_GAP)
            };
          });
          lane.next += lanes;
          bottom = blockTop + Math.min(perLane, n) * (T_NODE_H + ROW_GAP) - ROW_GAP;
          cursor += lanes * T_NODE_W + (lanes - 1) * INNER_GAP;
          placed = true;
        }
        g.children.forEach(function (child) {
          if (placed) cursor += INNER_GAP;
          var box = placeBox(child, cursor);
          cursor += box.width;
          top = Math.min(top, box.top);
          bottom = Math.max(bottom, box.bottom);
          placed = true;
        });
        return { width: cursor - left, top: top, bottom: bottom };
      }

      function placeBox(g, left) {
        var inner = placeGroup(g, left + PANEL_INSET);
        var y = inner.top - SUBFOLDER_HEADER;
        var height = inner.bottom + PANEL_INSET - y;
        var width = inner.width + PANEL_INSET * 2;
        panelBounds.push({ id: g.key, folder: col.folder, label: g.label, depth: g.depth,
          count: countOf(g), x: left, y: y, width: width, height: height });
        return { width: width, top: y, bottom: y + height };
      }

      var extent = placeGroup(col.root, x);
      var contentWidth = Math.max(extent.width, T_NODE_W);
      var height = countOf(col.root) > 0 ? extent.bottom + COLUMN_PAD_BOTTOM - 40 : 0;
      maxHeight = Math.max(maxHeight, height);
      columnBounds.push({ folder: col.folder, x: x - COLUMN_INSET, y: COLUMN_TOP,
        width: contentWidth + COLUMN_INSET * 2, height: height });
      x += contentWidth + COLUMN_GAP;
    });

    return { nodes: nodes, groups: columnBounds, panels: panelBounds,
      width: x - COLUMN_GAP + COLUMN_PADDING_X, height: maxHeight };
  }

  /* ------------------------------------------------------------- M layout */

  var M_NODE_W = 252, M_NODE_H = 84;
  var LAYER_INSET = 28, LAYER_GAP = 104, NODE_GAP_X = 40, NODE_GAP_Y = 28, CANVAS_PAD = 64;
  var LAYER_HEADER = HEADER_INSET * 2 + PILL_HEIGHT;
  var MAX_PER_LINE = 6;

  function lineCount(n) {
    if (n <= MAX_PER_LINE) return n > 0 ? 1 : 0;
    return Math.ceil(n / MAX_PER_LINE);
  }

  function layoutM(queries, layerOrder) {
    var byName = new Map(queries.map(function (q) { return [q.name, q]; }));
    var bandIndex = new Map(layerOrder.map(function (l, i) { return [l, i]; }));
    var byLayer = new Map(layerOrder.map(function (l) { return [l, []]; }));
    queries.forEach(function (q) {
      var list = byLayer.get(q.layer) || byLayer.get(layerOrder[layerOrder.length - 1]);
      if (list) list.push(q);
    });
    byLayer.forEach(function (list) {
      list.sort(function (a, b) { return compareText(a.name, b.name); });
    });

    var slotOf = new Map();
    layerOrder.forEach(function (l) {
      byLayer.get(l).forEach(function (q, slot) { slotOf.set(q.id, slot); });
    });

    function sweep(down) {
      var idx = layerOrder.map(function (_, i) { return i; });
      (down ? idx.slice(1) : idx.slice(0, -1).reverse()).forEach(function (i) {
        var list = byLayer.get(layerOrder[i]);
        if (list.length < 2) return;
        var scored = list.map(function (q) {
          var slots = (down ? q.dependsOn : q.usedBy)
            .map(function (n) { return byName.get(n); })
            .filter(function (n) { return !!n && bandIndex.get(n.layer) !== i; })
            .map(function (n) { return slotOf.get(n.id); })
            .filter(function (s) { return s !== undefined; });
          // A query with no neighbour keeps its slot rather than scoring zero,
          // which would drag every unconnected query to the left.
          var score = slots.length
            ? slots.reduce(function (a, b) { return a + b; }, 0) / slots.length
            : slotOf.get(q.id);
          return { q: q, score: score };
        });
        scored.sort(function (a, b) { return a.score - b.score || compareText(a.q.name, b.q.name); });
        var sorted = scored.map(function (s) { return s.q; });
        byLayer.set(layerOrder[i], sorted);
        sorted.forEach(function (q, slot) { slotOf.set(q.id, slot); });
      });
    }
    for (var pass = 0; pass < 3; pass++) { sweep(true); sweep(false); }

    // Shape every band before placing anything: the bands share a width, so the
    // widest layer sets the axis and narrower ones hang off its middle. Left-
    // aligning each band to its own content reads as a ladder down the left edge.
    var shapes = layerOrder.map(function (l) {
      var list = byLayer.get(l);
      var lines = lineCount(list.length);
      var perLine = lines > 0 ? Math.ceil(list.length / lines) : 0;
      var across = Math.min(perLine, list.length);
      return { layer: l, list: list, lines: lines, perLine: perLine,
        width: across > 0 ? across * M_NODE_W + (across - 1) * NODE_GAP_X : M_NODE_W };
    });
    var bandWidth = Math.max.apply(null,
      [M_NODE_W].concat(shapes.map(function (s) { return s.width; })));

    var nodes = {}, bands = [], y = CANVAS_PAD;
    shapes.forEach(function (shape) {
      var blockTop = y + LAYER_HEADER;
      var left = CANVAS_PAD + Math.round((bandWidth - shape.width) / 2);
      shape.list.forEach(function (q, i) {
        nodes[q.id] = {
          id: q.id,
          x: left + (i % shape.perLine) * (M_NODE_W + NODE_GAP_X),
          y: blockTop + Math.floor(i / shape.perLine) * (M_NODE_H + NODE_GAP_Y)
        };
      });
      var height = shape.list.length > 0
        ? LAYER_HEADER + shape.lines * M_NODE_H + (shape.lines - 1) * NODE_GAP_Y + LAYER_INSET
        : 0;
      bands.push({ folder: 'Layer ' + shape.layer, layer: shape.layer,
        x: CANVAS_PAD - LAYER_INSET, y: y, width: bandWidth + LAYER_INSET * 2,
        height: height, count: shape.list.length });
      y += height + LAYER_GAP;
    });

    return { nodes: nodes, groups: bands, panels: [],
      width: bandWidth + CANVAS_PAD * 2, height: y - LAYER_GAP + CANVAS_PAD };
  }

  /* ------------------------------------------------------------- the graph */

  var READABLE_ZOOM = 0.85, MIN_ZOOM = 0.2, MAX_ZOOM = 1.6;

  function makeGraph(mount, spec) {
    var isM = spec.kind === 'm';
    var NODE_W = isM ? M_NODE_W : T_NODE_W;
    var NODE_H = isM ? M_NODE_H : T_NODE_H;
    var ALL = spec.items;
    var byId = new Map(ALL.map(function (d) { return [d.id, d]; }));
    var byName = new Map(ALL.map(function (d) { return [d.name, d]; }));

    var st = { selectedId: null, hoveredId: null, focus: false, detailOpen: true,
      query: '', dark: isDark() };
    var themes = new Map();
    var layout = null, nodeEls = new Map(), edgeEls = [], built = null;
    var view = { x: 40, y: 12, z: 0.62 };

    function dirColor(k) { return DIRECTION[k][st.dark ? 'dark' : 'light']; }

    function buildThemes(groups) {
      themes = new Map();
      var i = 0;
      groups.forEach(function (g) {
        if (!isM && g === UNFOLDERED) {
          themes.set(g, Object.assign({ label: g }, st.dark ? NEUTRAL.dark : NEUTRAL.light));
          return;
        }
        var pair = PALETTE[i % PALETTE.length];
        themes.set(g, Object.assign({ label: g }, st.dark ? pair.dark : pair.light));
        i++;
      });
    }

    function themeOf(g) {
      return themes.get(g) || Object.assign({ label: g }, st.dark ? NEUTRAL.dark : NEUTRAL.light);
    }

    /** Focus mode: the selection plus its direct neighbours, nothing else. */
    function shown() {
      if (!st.focus || !st.selectedId) return { items: ALL, groups: spec.groups };
      var keep = new Set([st.selectedId]);
      var root = byId.get(st.selectedId);
      root.dependsOn.concat(root.usedBy).forEach(function (n) {
        var t = byName.get(n);
        if (t) keep.add(t.id);
      });
      var items = ALL.filter(function (d) { return keep.has(d.id); });
      var groups = isM
        ? Array.from(new Set(items.map(function (d) { return d.layer; }))).sort(function (a, b) { return a - b; })
        : Array.from(new Set(items.map(function (d) { return d.folder; }))).sort(compareFolders);
      return { items: items, groups: groups };
    }

    function matchSet() {
      return rank().set;
    }

    /**
     * Search is discovery, not filtering: the answer is usually one measure, and
     * it should be the first row. The apps sort suggestions alphabetically, which
     * puts `Avg Sales per Customer` above `Total Sales` for the query "sales" --
     * alphabetical is an order, not a ranking.
     *
     * Score, best first: an exact name beats a name starting with the query,
     * which beats the query as a whole word inside the name, which beats the name
     * containing it anywhere, which beats a hit somewhere else in the haystack
     * (folder, table, description, step name, native SQL). Shorter names win
     * ties, because a short name containing the query is more likely to BE it.
     */
    var cachedRank = { q: null, set: null, rows: [] };

    function scoreOf(d, q) {
      var name = d.name.toLowerCase();
      if (name === q) return 0;
      // Starts-with and ends-with share a band on purpose. Lexically `Sales PY`
      // starts with "sales" and `Total Sales` does not, so a strict prefix rule
      // ranks the qualifier above the thing being qualified. Both are "this
      // measure, plus a word", and which one you meant is decided below by how
      // much of the model leans on it -- not by which end the word sits at.
      // The obvious suffix test is `indexOf(' ' + q) === name.length - q.length - 1`,
      // and it is wrong: a miss returns -1, and for a name exactly as long as the
      // query the arithmetic is also -1, so every same-length name matched. That
      // is how `ASP PY` came back as a top hit for "budget".
      var endsWithWord = name.length > q.length &&
        name.slice(name.length - q.length - 1) === ' ' + q;
      if (name.indexOf(q + ' ') === 0 || endsWithWord) return 1;
      if (name.indexOf(q) === 0 || name.indexOf(' ' + q + ' ') !== -1) return 2;
      if (name.indexOf(q) !== -1) return 3;
      if (d.search.indexOf(q) !== -1) return 4;
      return -1;
    }

    var WHY = ['exact match', 'name is this + a word', 'name contains the word',
      'name contains', 'in its detail'];

    function rank() {
      var q = st.query.trim().toLowerCase();
      if (cachedRank.q === q) return cachedRank;
      if (!q) {
        cachedRank = { q: q, set: null, rows: [] };
        return cachedRank;
      }
      var scored = [];
      var set = new Set();
      ALL.forEach(function (d) {
        var sc = scoreOf(d, q);
        if (sc < 0) return;
        set.add(d.id);
        scored.push({ d: d, sc: sc });
      });
      // Within a band, prominence decides. A measure 34 others read is far more
      // likely to be the one meant than a same-scoring leaf, and prominence is a
      // fact the parser already emits. Length breaks the remaining ties, because
      // a short name carrying the query is more likely to BE the thing.
      scored.sort(function (a, b) {
        return a.sc - b.sc ||
          (b.d.usedBy.length - a.d.usedBy.length) ||
          a.d.name.length - b.d.name.length ||
          compareText(a.d.name, b.d.name);
      });
      cachedRank = {
        q: q, set: set,
        rows: scored.map(function (r) { return { d: r.d, why: WHY[r.sc] }; })
      };
      return cachedRank;
    }

    /* -- chrome ---------------------------------------------------------- */

    var search = h('input', {
      type: 'search', class: 'dcvg-search-input', placeholder: spec.searchPlaceholder,
      autocomplete: 'off', spellcheck: 'false',
      oninput: function (e) {
        st.query = e.target.value; activeRow = 0; syncSearch(); paint();
      },
      onkeydown: function (e) {
        if (e.key === 'Escape') {
          st.query = ''; search.value = ''; activeRow = 0; syncSearch(); paint();
          return;
        }
        var rows = results.querySelectorAll('.dcvg-result');
        if (!rows.length) return;
        if (e.key === 'ArrowDown') {
          e.preventDefault();
          activeRow = Math.min(activeRow + 1, rows.length - 1);
          syncSearch();
        } else if (e.key === 'ArrowUp') {
          e.preventDefault();
          activeRow = Math.max(activeRow - 1, 0);
          syncSearch();
        } else if (e.key === 'Enter') {
          e.preventDefault();
          if (rows[activeRow]) rows[activeRow].click();
        }
      }
    });
    var searchCount = h('span', { class: 'dcvg-search-count' });
    var results = h('div', { class: 'dcvg-results', hidden: true });

    var focusBtn = h('button', {
      type: 'button', class: 'dcvg-btn', 'aria-pressed': 'false',
      onclick: function () {
        if (!st.selectedId) return;
        st.focus = !st.focus;
        // Entering focus collapses the pane. Focus exists to read the shape of a
        // chain, and the pane covers a third of the canvas at exactly the moment
        // the canvas became worth looking at. Leaving focus restores it, because
        // out here the pane is the reason you clicked in the first place.
        st.detailOpen = !st.focus;
        built = null;
        sync();
        fit();
        syncChrome();
        renderDetail();
      }
    }, ['Focus']);

    /* The way back to a pane that focus collapsed. Sits in the corner the pane
       would occupy, so the control and the thing it opens share a place. */
    var detailTab = h('button', {
      type: 'button', class: 'dcvg-detail-tab', hidden: true,
      title: 'Show details', 'aria-label': 'Show details',
      onclick: function () { st.detailOpen = true; syncChrome(); renderDetail(); }
    }, ['‹']);

    function iconBtn(label, glyph, fn) {
      return h('button', { type: 'button', class: 'dcvg-icon', title: label,
        'aria-label': label, onclick: fn }, [glyph]);
    }

    var world = h('div', { class: 'dcvg-world' });
    var canvas = h('div', { class: 'dcvg-canvas', tabindex: '0' }, [world]);
    var detail = h('aside', { class: 'dcvg-detail', hidden: true });

    var toolbar = h('div', { class: 'dcvg-toolbar' }, [
      h('div', { class: 'dcvg-search' }, [search, searchCount]),
      h('div', { class: 'dcvg-tools' }, [
        focusBtn,
        iconBtn('Zoom out', '−', function () { zoomBy(1 / 1.2); }),
        iconBtn('Zoom in', '+', function () { zoomBy(1.2); }),
        iconBtn('Fit to view', '⤢', fit),
        iconBtn('Clear selection', '×', function () { select(null); })
      ])
    ]);

    var hint = h('p', { class: 'dcvg-hint', text: 'Drag to pan · Ctrl or pinch to zoom · click a ' +
      (isM ? 'query' : 'measure') + ' for its detail' });

    mount.appendChild(h('div', { class: 'dcvg' }, [
      toolbar, results,
      h('div', { class: 'dcvg-stage' }, [canvas, detail, detailTab]),
      hint
    ]));

    /* -- pan and zoom ----------------------------------------------------- */

    var dragging = null;
    // Plain wheel is left alone: this canvas lives inside a scrolling document,
    // and swallowing the wheel would trap the page the moment the pointer
    // crossed a graph. Ctrl+wheel (and a trackpad pinch, which arrives as the
    // same event) zooms.
    canvas.addEventListener('wheel', function (e) {
      if (!e.ctrlKey && !e.metaKey) return;
      e.preventDefault();
      zoomAt(e.clientX, e.clientY, Math.exp(-e.deltaY * 0.002));
    }, { passive: false });

    canvas.addEventListener('pointerdown', function (e) {
      if (e.button !== 0 || e.target.closest('.dcvg-node')) return;
      dragging = { id: e.pointerId, x: e.clientX, y: e.clientY, moved: false };
      canvas.setPointerCapture(e.pointerId);
      canvas.classList.add('dragging');
      setHovered(null);
    });
    canvas.addEventListener('pointermove', function (e) {
      if (!dragging || dragging.id !== e.pointerId) return;
      view.x += e.clientX - dragging.x;
      view.y += e.clientY - dragging.y;
      dragging.x = e.clientX; dragging.y = e.clientY; dragging.moved = true;
      applyTransform();
    });
    function endDrag(e) {
      if (!dragging || dragging.id !== e.pointerId) return;
      var moved = dragging.moved;
      dragging = null;
      canvas.classList.remove('dragging');
      // A drag is a pan; only a click on empty canvas clears the selection.
      if (!moved && !e.target.closest('.dcvg-node')) select(null);
    }
    canvas.addEventListener('pointerup', endDrag);
    canvas.addEventListener('pointercancel', endDrag);
    canvas.addEventListener('mouseleave', function () { setHovered(null); });
    canvas.addEventListener('keydown', function (e) {
      if (e.key !== 'Enter' && e.key !== ' ') return;
      var node = e.target.closest('.dcvg-node');
      if (!node) return;
      e.preventDefault();
      var id = node.getAttribute('data-id');
      select(id === st.selectedId ? null : id);
    });

    function applyTransform() {
      world.style.transform = 'translate(' + view.x + 'px,' + view.y + 'px) scale(' + view.z + ')';
    }
    function zoomAt(cx, cy, factor) {
      var r = canvas.getBoundingClientRect();
      var px = cx - r.left, py = cy - r.top;
      var next = clamp(view.z * factor, MIN_ZOOM, MAX_ZOOM);
      var ratio = next / view.z;
      view.x = px - (px - view.x) * ratio;
      view.y = py - (py - view.y) * ratio;
      view.z = next;
      applyTransform();
    }
    function zoomBy(f) {
      var r = canvas.getBoundingClientRect();
      zoomAt(r.left + r.width / 2, r.top + r.height / 2, f);
    }
    function fit() {
      if (!layout || !layout.height) return;
      var r = canvas.getBoundingClientRect();
      if (!r.width) return;
      var pad = 0.14;
      var z = clamp(Math.min(r.width / (layout.width * (1 + pad)),
        r.height / (layout.height * (1 + pad))), MIN_ZOOM, 1);
      view.z = z;
      view.x = (r.width - layout.width * z) / 2;
      view.y = (r.height - layout.height * z) / 2;
      applyTransform();
    }
    function centerOn(id) {
      var pos = layout && layout.nodes[id];
      if (!pos) return;
      var r = canvas.getBoundingClientRect();
      view.z = Math.max(view.z, READABLE_ZOOM);
      view.x = r.width / 2 - (pos.x + NODE_W / 2) * view.z;
      view.y = r.height / 2 - (pos.y + NODE_H / 2) * view.z;
      applyTransform();
    }

    /* -- selection -------------------------------------------------------- */

    function select(id) {
      st.selectedId = id;
      // A selection reopens the pane, except while focused, where the reader has
      // already said the canvas is what they want.
      if (id && !st.focus) st.detailOpen = true;
      if (!id && st.focus) { st.focus = false; built = null; sync(); }
      if (id && st.focus) { built = null; sync(); fit(); }
      else if (id) centerOn(id);
      syncChrome();
      paint();
      renderDetail();
    }

    function setHovered(id) {
      if (st.hoveredId === id) return;
      st.hoveredId = id;
      paint();
    }

    function navigateTo(name) {
      var target = byName.get(name);
      if (!target) return;
      // Focus mode narrows to one neighbourhood; stepping to a measure outside it
      // has to widen the graph first or the click does nothing visible.
      if (st.focus) { built = null; st.selectedId = target.id; sync(); fit(); }
      else { st.selectedId = target.id; centerOn(target.id); }
      syncChrome(); paint(); renderDetail();
    }

    /* -- build ------------------------------------------------------------ */

    function bezier(from, to) {
      if (isM) {
        var sx = from.x + NODE_W / 2, sy = from.y + NODE_H;
        var tx = to.x + NODE_W / 2, ty = to.y;
        var m = (sy + ty) / 2;
        return 'M' + sx + ',' + sy + ' C' + sx + ',' + m + ' ' + tx + ',' + m + ' ' + tx + ',' + ty;
      }
      var ax = from.x + NODE_W, ay = from.y + NODE_H / 2;
      var bx = to.x, by = to.y + NODE_H / 2;
      // React Flow's getBezierPath offset rule: half the horizontal distance
      // forwards, a square-root curve when the edge runs backwards, so a
      // backwards chain bows out instead of doubling back over itself.
      var off = function (d) { return d >= 0 ? 0.5 * d : 0.32 * 25 * Math.sqrt(-d); };
      return 'M' + ax + ',' + ay + ' C' + (ax + off(bx - ax)) + ',' + ay + ' ' +
        (bx - off(bx - ax)) + ',' + by + ' ' + bx + ',' + by;
    }

    function pill(label, count, theme) {
      return h('div', { class: 'dcvg-pill', style: { borderColor: theme.border } }, [
        h('span', { class: 'dcvg-pill-dot', style: { background: theme.dot } }),
        h('span', { class: 'dcvg-pill-label', style: { color: theme.text }, title: label, text: label }),
        h('span', { class: 'dcvg-pill-count', text: String(count) })
      ]);
    }

    function nodeEl(d, pos) {
      var meta = h('span', { class: 'dcvg-node-counts' });
      if (d.isHidden) meta.appendChild(h('span', { class: 'dcvg-flag', title: 'Hidden in the model', text: '◌' }));
      if (d.dependsOn.length) {
        meta.appendChild(h('span', { class: 'dcvg-count', style: { color: dirColor('upstream') },
          title: d.dependsOn.length + ' upstream' }, ['↑' + d.dependsOn.length]));
      }
      if (d.usedBy.length) {
        meta.appendChild(h('span', { class: 'dcvg-count', style: { color: dirColor('downstream') },
          title: d.usedBy.length + ' downstream' }, ['↓' + d.usedBy.length]));
      }
      var kids = [h('p', { class: 'dcvg-node-name', title: d.name }, [meta, d.name])];
      if (isM) kids.push(h('p', { class: 'dcvg-node-sub', text: d.sub }));
      return h('div', {
        class: 'dcvg-node', 'data-id': d.id, tabindex: '0', role: 'button',
        'aria-label': d.name + ', ' + d.dependsOn.length + ' upstream, ' + d.usedBy.length + ' downstream',
        style: { left: pos.x + 'px', top: pos.y + 'px', width: NODE_W + 'px', height: NODE_H + 'px' },
        onclick: function () { select(d.id === st.selectedId ? null : d.id); },
        onmouseenter: function () { setHovered(d.id); },
        onmouseleave: function () { setHovered(null); }
      }, kids);
    }

    function sync() {
      var s = shown();
      if (built === s.items) { paint(); return; }
      built = s.items;
      buildThemes(isM ? s.groups.map(function (l) { return 'Layer ' + l; }) : s.groups);
      layout = isM ? layoutM(s.items, s.groups) : layoutTmdl(s.items, s.groups);

      clear(world);
      nodeEls = new Map(); edgeEls = [];
      world.style.width = layout.width + 'px';
      world.style.height = layout.height + 'px';

      var svg = svgEl('svg', { class: 'dcvg-edges', width: layout.width, height: layout.height,
        viewBox: '0 0 ' + layout.width + ' ' + layout.height });
      world.appendChild(svg);

      layout.groups.filter(function (g) { return g.height > 0; }).forEach(function (g) {
        var theme = themeOf(g.folder);
        var count = g.count !== undefined ? g.count : s.items.filter(function (d) {
          return d.folder === g.folder;
        }).length;
        world.appendChild(h('div', { class: 'dcvg-group', style: {
          left: g.x + 'px', top: g.y + 'px', width: g.width + 'px', height: g.height + 'px',
          background: isM ? 'transparent' : theme.tint,
          borderColor: isM ? 'var(--color-border)' : theme.border
        } }, [
          h('div', { class: 'dcvg-band', style: { height: SUBFOLDER_HEADER + 'px',
            paddingLeft: (isM ? LAYER_INSET : COLUMN_INSET) + 'px',
            justifyContent: isM ? 'flex-start' : 'center' } },
            [pill(isM ? 'Layer ' + g.layer : theme.label, count, theme)])
        ]));
      });

      // A nested box must paint over the one wrapping it, so depth IS the z-order.
      layout.panels.forEach(function (p) {
        var theme = themeOf(p.folder);
        var step = Math.min(p.depth, 3);
        world.appendChild(h('div', { class: 'dcvg-subgroup', style: {
          left: p.x + 'px', top: p.y + 'px', width: p.width + 'px', height: p.height + 'px',
          zIndex: String(p.depth),
          background: st.dark
            ? 'color-mix(in srgb, #ffffff ' + step * 5 + '%, ' + theme.tint + ')'
            : 'color-mix(in srgb, ' + theme.border + ' ' + step * 45 + '%, ' + theme.tint + ')',
          borderColor: st.dark
            ? 'color-mix(in srgb, #ffffff ' + (8 + step * 8) + '%, ' + theme.tint + ')'
            : 'color-mix(in srgb, ' + theme.border + ' ' + (45 + step * 30) + '%, ' + theme.tint + ')'
        } }, [
          h('div', { class: 'dcvg-band', style: { height: SUBFOLDER_HEADER + 'px',
            paddingLeft: PANEL_INSET + 'px', justifyContent: 'flex-start' } },
            [pill(p.label, p.count, theme)])
        ]));
      });

      s.items.forEach(function (d) {
        var el = nodeEl(d, layout.nodes[d.id]);
        nodeEls.set(d.id, el);
        world.appendChild(el);
      });

      var visible = new Set(s.items.map(function (d) { return d.id; }));
      s.items.forEach(function (d) {
        d.dependsOn.forEach(function (n) {
          var dep = byName.get(n);
          if (!dep || !visible.has(dep.id)) return;
          var from = layout.nodes[dep.id], to = layout.nodes[d.id];
          if (!from || !to) return;
          var p = svgEl('path', { class: 'dcvg-edge', d: bezier(from, to), fill: 'none' });
          svg.appendChild(p);
          edgeEls.push({ el: p, source: dep.id, target: d.id });
        });
      });

      applyTransform();
      paint();
    }

    /**
     * The block's fill, composited rather than layered. A translucent wash is
     * the right look but shows the dependency edges running underneath it —
     * invisible in light mode at 90% white, obvious in dark at 13%.
     */
    function fillOf(tint, sel) {
      var white = st.dark ? (sel ? 22 : 13) : sel ? 96 : 90;
      return 'color-mix(in srgb, #ffffff ' + white + '%, ' + tint + ')';
    }

    var RESTING = '0 1px 2px rgb(0 0 0 / 0.04), 0 8px 20px -12px rgb(0 0 0 / 0.14)';

    function paint() {
      if (!built) return;
      var s = shown();
      // A selection holds the canvas until cleared: hovering elsewhere no longer
      // repaints the chain out from under you.
      var anchorId = st.selectedId || st.hoveredId;
      var anchor = anchorId ? byId.get(anchorId) : null;
      var up = new Set(), down = new Set();
      if (anchor) {
        anchor.dependsOn.forEach(function (n) { var t = byName.get(n); if (t) up.add(t.id); });
        anchor.usedBy.forEach(function (n) { var t = byName.get(n); if (t) down.add(t.id); });
      }
      var matches = matchSet();
      // A search matching nothing deliberately does not dim: fading every block
      // conveys nothing, and the count says it properly.
      var dimming = matches && matches.size > 0;

      s.items.forEach(function (d) {
        var el = nodeEls.get(d.id);
        if (!el) return;
        var theme = themeOf(isM ? 'Layer ' + d.layer : d.folder);
        var sel = d.id === st.selectedId;
        var kind = anchorId === d.id ? 'selected'
          : up.has(d.id) ? 'upstream' : down.has(d.id) ? 'downstream' : 'none';
        var ring = sel ? dirColor('selected') : kind === 'none' ? null : dirColor(kind);
        el.style.backgroundColor = fillOf(theme.tint, sel);
        el.style.borderColor = theme.border;
        el.style.boxShadow = !ring ? RESTING
          : sel ? '0 0 0 3px ' + ring + ', 0 0 22px 2px ' + ring + '66, 0 22px 44px -20px rgb(0 0 0 / 0.38)'
            : '0 0 0 2px ' + ring + ', 0 10px 24px -14px rgb(0 0 0 / 0.24)';
        el.style.zIndex = kind === 'none' ? '8' : '9';
        // A search miss is dimmed rather than a hit outlined: an outline speaks
        // the same visual language as hover and selection, and two unrelated
        // things saying "look at me" the same way is unreadable.
        el.classList.toggle('dimmed', !!(dimming && !matches.has(d.id)));
      });

      edgeEls.forEach(function (e) {
        var kind = 'idle';
        if (anchorId) {
          if (e.target === anchorId) kind = 'upstream';
          else if (e.source === anchorId) kind = 'downstream';
        }
        var active = kind !== 'idle';
        e.el.setAttribute('stroke', dirColor(active ? kind : 'edge'));
        e.el.setAttribute('stroke-width', active ? '2' : '1.4');
        // Never dimmed. Fading non-matching edges erases the chains — the one
        // thing this exists to show — exactly when you are looking for something.
        e.el.setAttribute('opacity', active ? '0.95' : '0.7');
        if (active) e.el.setAttribute('stroke-dasharray', '7 5');
        else e.el.removeAttribute('stroke-dasharray');
      });
    }

    /* -- detail panel ----------------------------------------------------- */

    function refSection(title, names, accent, empty) {
      return h('div', { class: 'dcvg-section' }, [
        h('p', { class: 'dcvg-section-title', style: { color: accent }, text: title }),
        names.length === 0
          ? h('p', { class: 'dcvg-section-empty', text: empty })
          : h('div', { class: 'dcvg-chips' }, names.map(function (n) {
              var known = byName.has(n);
              return h('button', {
                type: 'button', class: 'dcvg-ref' + (known ? '' : ' unknown'), text: n,
                title: known ? 'Go to ' + n : n + ' — not in this parse',
                style: { borderColor: accent + '55', backgroundColor: accent + '14' },
                onclick: function () { navigateTo(n); }
              });
            }))
      ]);
    }

    function renderDetail() {
      var d = st.selectedId ? byId.get(st.selectedId) : null;
      detailTab.hidden = !(d && !st.detailOpen);
      if (!d || !st.detailOpen) { detail.hidden = true; clear(detail); return; }
      detail.hidden = false;
      clear(detail);
      var theme = themeOf(isM ? 'Layer ' + d.layer : d.folder);
      var head = h('div', { class: 'dcvg-detail-head' }, [
        h('span', { class: 'dcvg-folder-chip',
          style: { background: theme.tint, color: theme.text, borderColor: theme.border },
          text: isM ? 'Layer ' + d.layer + ' · ' + d.kind : (d.folderPath || []).join(' / ') }),
        h('button', { type: 'button', class: 'dcvg-close', 'aria-label': 'Close details',
          // Collapses the pane only. Deselecting is what the canvas and the node
          // itself are for -- while focused, closing must not throw away the
          // chain being read.
          onclick: function () { st.detailOpen = false; syncChrome(); renderDetail(); } },
          ['×'])
      ]);
      var body = [head, h('h4', { class: 'dcvg-detail-name', text: d.name })];

      (d.meta || []).forEach(function (m) {
        body.push(h('p', { class: 'dcvg-detail-meta' }, [
          h('span', { class: 'dcvg-detail-key', text: m[0] }),
          h('span', { text: m[1] })
        ]));
      });
      if (d.description) body.push(h('p', { class: 'dcvg-detail-desc', text: d.description }));
      if (d.tables && d.tables.length) {
        body.push(h('div', { class: 'dcvg-chips' }, d.tables.map(function (t) {
          return h('span', { class: 'dcvg-tag', text: t });
        })));
      }
      if (d.code) {
        body.push(h('p', { class: 'dcvg-section-title', text: spec.codeLabel }));
        var pre = h('pre', { class: 'dcvg-code' });
        pre.innerHTML = d.codeHtml || '';
        if (!d.codeHtml) pre.textContent = d.code;
        body.push(pre);
      }
      if (d.list && d.list.length) {
        body.push(h('p', { class: 'dcvg-section-title', text: spec.listLabel }));
        body.push(h('ol', { class: 'dcvg-steps' }, d.list.map(function (s) {
          return h('li', { class: s[2] ? 'dead' : '' }, [
            h('b', { text: s[0] }), h('span', { text: s[1] })
          ]);
        })));
      }
      body.push(refSection(spec.upLabel, d.dependsOn, dirColor('upstream'), spec.upEmpty));
      body.push(refSection(spec.downLabel, d.usedBy, dirColor('downstream'), spec.downEmpty));
      detail.appendChild(h('div', { class: 'dcvg-detail-body' }, body));
    }

    /* -- search results --------------------------------------------------- */

    var SUGGEST_LIMIT = 8;
    var activeRow = 0;

    function syncSearch() {
      var q = st.query.trim();
      var hits = rank().rows;
      if (!q) {
        searchCount.textContent = '';
        results.hidden = true;
        clear(results);
        return;
      }
      searchCount.textContent = hits.length + ' of ' + ALL.length;
      clear(results);
      results.hidden = false;
      if (!hits.length) {
        results.appendChild(h('p', { class: 'dcvg-noresult',
          text: 'Nothing matches “' + q + '”.' }));
        return;
      }
      activeRow = Math.min(activeRow, hits.length - 1);
      hits.slice(0, SUGGEST_LIMIT).forEach(function (row, i) {
        var d = row.d;
        var theme = themeOf(isM ? 'Layer ' + d.layer : d.folder);
        results.appendChild(h('button', {
          type: 'button',
          class: 'dcvg-result' + (i === activeRow ? ' active' : ''),
          // Re-rendering the list on mouseenter replaces the row between mousedown
          // and click, so hovering a suggestion and clicking it does nothing at
          // all. Move the marker by class, never by rebuilding.
          onmouseenter: function (e) {
            var prev = results.querySelector('.dcvg-result.active');
            if (prev) prev.classList.remove('active');
            e.currentTarget.classList.add('active');
            activeRow = i;
          },
          onclick: function () { navigateTo(d.name); }
        }, [
          h('span', { class: 'dcvg-result-dot', style: { background: theme.dot } }),
          h('span', { class: 'dcvg-result-name', text: d.name }),
          h('span', { class: 'dcvg-result-meta', text: isM ? 'Layer ' + d.layer : d.folder }),
          h('span', { class: 'dcvg-result-why', text: row.why })
        ]));
      });
      if (hits.length > SUGGEST_LIMIT) {
        results.appendChild(h('span', { class: 'dcvg-result-more',
          text: '+' + (hits.length - SUGGEST_LIMIT) + ' more' }));
      }
    }

    function syncChrome() {
      focusBtn.disabled = !st.selectedId;
      focusBtn.setAttribute('aria-pressed', String(st.focus));
      focusBtn.classList.toggle('on', st.focus);
      focusBtn.textContent = st.focus ? 'Focused' : 'Focus';
    }

    /* -- theme ------------------------------------------------------------ */

    // The palette has explicit dark values rather than an opacity trick, so a
    // theme flip is a rebuild, not a restyle.
    var obs = new MutationObserver(function () {
      var next = isDark();
      if (next === st.dark) return;
      st.dark = next;
      built = null;
      sync();
      renderDetail();
    });
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] });
    obs.observe(document.body, { attributes: true, attributeFilter: ['class'] });

    /**
     * Opening view. Fitting the whole model is the obvious default and it is
     * wrong for a wide one: 142 measures is ~8000px, which fits at z=0.13 and
     * shows a wall of unreadable confetti. The apps do not fit at all -- they
     * open at 0.72 in the top-left and let you pan.
     *
     * So: fit only when the fitted zoom would still be legible, and otherwise
     * open at the apps own resting view. A small graph gets framed, a large one
     * gets a readable starting corner.
     */
    var LEGIBLE_FIT = 0.45;

    function openingView() {
      var r = canvas.getBoundingClientRect();
      if (!r.width || !layout || !layout.height) return;
      var pad = 0.14;
      var z = Math.min(r.width / (layout.width * (1 + pad)),
        r.height / (layout.height * (1 + pad)));
      if (z >= LEGIBLE_FIT) { fit(); return; }
      view = { x: 40, y: 12, z: 0.72 };
      applyTransform();
    }

    sync();
    syncChrome();
    // Wait for a real width; a graph built inside a not-yet-laid-out block would
    // measure itself against a zero rect.
    if (window.ResizeObserver) {
      var ro = new ResizeObserver(function () {
        if (canvas.getBoundingClientRect().width > 0) { openingView(); ro.disconnect(); }
      });
      ro.observe(canvas);
    } else {
      setTimeout(openingView, 60);
    }
  }

  /* --------------------------------------------------------------- boot */

  /* Shared with dcv-chain.js. Exported rather than duplicated: two copies of the
     palette or the band arithmetic would drift, and the whole argument for
     porting the app instead of redrawing it was that one implementation beats
     two that can disagree. */
  window.dcvShared = {
    h: h, svgEl: svgEl, clear: clear, clamp: clamp, compareText: compareText,
    PALETTE: PALETTE, NEUTRAL: NEUTRAL, DIRECTION: DIRECTION, isDark: isDark,
    layoutM: layoutM, M_NODE_W: M_NODE_W, M_NODE_H: M_NODE_H
  };

  window.dcvGraph = function (mountId, dataId, spec) {
    var mount = document.getElementById(mountId);
    var raw = document.getElementById(dataId);
    if (!mount || !raw) return;
    var data = JSON.parse(raw.textContent);
    makeGraph(mount, Object.assign({ items: data.items, groups: data.groups }, spec));
  };
})();
