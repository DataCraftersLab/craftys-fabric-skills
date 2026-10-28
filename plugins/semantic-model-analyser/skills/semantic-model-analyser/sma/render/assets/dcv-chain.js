/**
 * dcv-chain — the labelled measure chain, with a folder rail to pick from and a
 * popover to read one node without losing the picture.
 *
 * A different job from the lineage canvas next door. That one answers "what does
 * this model look like"; this one answers "where does THIS measure come from".
 * So it draws one measure's chain rather than the whole graph, labels every node
 * because there are few enough to label, and lays the bands out by distance from
 * the measure you picked rather than by folder.
 *
 * The detail is a POPOVER, not a side panel. A popover exists for exactly this: someone
 * comparing eight nodes wants the same four fields eight times, quickly, without
 * losing their place ${a side panel covers a third of a chain that is only
 * interesting as a whole}. It is a peek, so it carries a subset and offers the
 * way through to the whole record.
 *
 * Everything shared with the canvas comes from window.dcvShared rather than being
 * copied ${two palettes drift, and the point of porting the app was one
 * implementation instead of two that can disagree}.
 */
(function () {
  'use strict';

  var S = window.dcvShared;
  var h = S.h, svgEl = S.svgEl, clear = S.clear, clamp = S.clamp;
  var compareText = S.compareText, PALETTE = S.PALETTE, NEUTRAL = S.NEUTRAL;
  var DIRECTION = S.DIRECTION, isDark = S.isDark;
  var NODE_W = S.M_NODE_W, NODE_H = 62;

  var UNFOLDERED = 'Other';
  var MIN_ZOOM = 0.25, MAX_ZOOM = 1.6;

  /* Band geometry. Shallower than the lineage canvas because a chain is a dozen
     nodes, not a hundred, and the vertical run is what has to stay on screen. */
  var PAD = 48, BAND_GAP = 58, GAP_X = 34;

  function makeChain(mount, data) {
    var ALL = data.items;
    var byName = new Map(ALL.map(function (d) { return [d.name, d]; }));
    var st = { pick: null, dir: 'up', dark: isDark(), filter: '' };
    var themes = new Map();
    var layout = null, nodeEls = new Map(), edgeEls = [];
    var view = { x: 0, y: 0, z: 0.9 };
    var popFor = null;

    function dirColor(k) { return DIRECTION[k][st.dark ? 'dark' : 'light']; }

    function buildThemes() {
      themes = new Map();
      var folders = Array.from(new Set(ALL.map(function (d) { return d.folder; })))
        .sort(function (a, b) {
          if (a === UNFOLDERED) return b === UNFOLDERED ? 0 : 1;
          if (b === UNFOLDERED) return -1;
          return compareText(a, b);
        });
      var i = 0;
      folders.forEach(function (f) {
        if (f === UNFOLDERED) {
          themes.set(f, st.dark ? NEUTRAL.dark : NEUTRAL.light);
          return;
        }
        themes.set(f, (PALETTE[i % PALETTE.length])[st.dark ? 'dark' : 'light']);
        i++;
      });
    }

    function themeOf(f) { return themes.get(f) || (st.dark ? NEUTRAL.dark : NEUTRAL.light); }

    /**
     * The chain of one measure, and how far each member sits from it.
     *
     * Distance is the LONGEST path, not the shortest: a measure reachable both
     * directly and through three hops belongs on the far band, or its edges run
     * backwards through the picture and the bands stop meaning anything.
     */
    function chainOf(name, dir) {
      var depth = new Map([[name, 0]]);
      var order = [name];
      for (var i = 0; i < order.length; i++) {
        var cur = byName.get(order[i]);
        if (!cur) continue;
        var next = dir === 'down' ? cur.usedBy
          : dir === 'up' ? cur.dependsOn
            : cur.dependsOn.concat(cur.usedBy);
        next.forEach(function (n) {
          if (!byName.has(n)) return;
          var d = depth.get(order[i]) + 1;
          if (!depth.has(n)) { depth.set(n, d); order.push(n); }
          else if (d > depth.get(n) && depth.get(n) !== 0) { depth.set(n, d); order.push(n); }
        });
        if (order.length > 400) break;
      }
      return depth;
    }

    function shown() {
      if (!st.pick || !byName.has(st.pick)) return { items: [], bands: [] };
      var depth = chainOf(st.pick, st.dir);
      var maxD = 0;
      depth.forEach(function (d) { maxD = Math.max(maxD, d); });
      var items = [];
      depth.forEach(function (d, n) {
        var src = byName.get(n);
        // Upstream reads bottom-up: sources at the top, the measure you picked at
        // the foot. Downstream reads the other way, which is the same rule --
        // what feeds a thing sits above it.
        var band = st.dir === 'down' ? d : maxD - d;
        items.push(Object.assign({}, src, { layer: band }));
      });
      var bands = [];
      for (var b = 0; b <= maxD; b++) bands.push(b);
      return { items: items, bands: bands, maxD: maxD, depth: depth };
    }

    /* ------------------------------------------------------------- chrome */

    var rail = h('div', { class: 'dcvc-rail' });
    var world = h('div', { class: 'dcvc-world' });
    var canvas = h('div', { class: 'dcvc-canvas', tabindex: '0' }, [world]);
    var pop = h('div', { class: 'dcvc-pop', role: 'dialog', hidden: true });
    var full = h('aside', { class: 'dcvg-detail dcvc-full', hidden: true });
    var empty = h('p', { class: 'dcvc-empty',
      text: 'Pick a measure on the left to draw its chain.' });

    var filterInput = h('input', {
      type: 'search', class: 'dcvc-filter', placeholder: 'Filter measures…',
      autocomplete: 'off', spellcheck: 'false',
      oninput: function (e) { st.filter = e.target.value.toLowerCase(); renderRail(); }
    });

    function dirBtn(id, label) {
      return h('button', {
        type: 'button', class: 'dcvc-seg' + (st.dir === id ? ' on' : ''),
        'aria-pressed': String(st.dir === id),
        onclick: function () { st.dir = id; closePop(); draw(); syncChrome(); }
      }, [label]);
    }

    var segs = h('div', { class: 'dcvc-segs' });

    function syncChrome() {
      clear(segs);
      [['up', 'Upstream'], ['both', 'Both'], ['down', 'Downstream']].forEach(function (o) {
        segs.appendChild(dirBtn(o[0], o[1]));
      });
      var s = shown();
      count.textContent = st.pick
        ? s.items.length + ' measures over ' + (s.bands.length) + ' bands'
        : '';
    }

    var count = h('span', { class: 'dcvc-count' });

    mount.appendChild(h('div', { class: 'dcvc' }, [
      h('div', { class: 'dcvc-bar' }, [segs, count]),
      h('div', { class: 'dcvc-stage' }, [
        h('div', { class: 'dcvc-side' }, [
          h('div', { class: 'dcvc-side-head' }, [filterInput]),
          rail
        ]),
        h('div', { class: 'dcvc-main' }, [canvas, pop, full, empty])
      ])
    ]));

    /* --------------------------------------------------------------- rail */

    function renderRail() {
      clear(rail);
      var groups = new Map();
      ALL.forEach(function (d) {
        if (st.filter && d.name.toLowerCase().indexOf(st.filter) === -1) return;
        if (!groups.has(d.folder)) groups.set(d.folder, []);
        groups.get(d.folder).push(d);
      });
      if (!groups.size) {
        rail.appendChild(h('p', { class: 'dcvc-rail-empty', text: 'No measure matches.' }));
        return;
      }
      Array.from(groups.keys()).sort(function (a, b) {
        if (a === UNFOLDERED) return b === UNFOLDERED ? 0 : 1;
        if (b === UNFOLDERED) return -1;
        return compareText(a, b);
      }).forEach(function (folder) {
        var list = groups.get(folder).sort(function (a, b) { return compareText(a.name, b.name); });
        var theme = themeOf(folder);
        var holdsPick = list.some(function (d) { return d.name === st.pick; });
        var body = h('ul', { class: 'dcvc-rail-list' }, list.map(function (d) {
          return h('li', {}, [h('button', {
            type: 'button',
            class: 'dcvc-rail-item' + (d.name === st.pick ? ' on' : ''),
            title: d.name,
            onclick: function () { pickMeasure(d.name); }
          }, [
            h('span', { class: 'dcvc-rail-dot', style: { background: theme.dot } }),
            h('span', { class: 'dcvc-rail-name', text: d.name }),
            d.usedBy.length
              ? h('span', { class: 'dcvc-rail-n', text: String(d.usedBy.length) })
              : null
          ])]);
        }));
        // A filter that hits opens the folders it hit, and so does the folder
        // holding the current pick. Everything else stays as the reader left it.
        var det = h('details', { class: 'dcvc-rail-group' }, [
          h('summary', {}, [
            h('span', { class: 'dcvc-rail-dot', style: { background: theme.dot } }),
            h('span', { class: 'dcvc-rail-folder', text: folder }),
            h('span', { class: 'dcvc-rail-n', text: String(list.length) })
          ]),
          body
        ]);
        if (holdsPick || st.filter) det.open = true;
        rail.appendChild(det);
      });
    }

    function pickMeasure(name) {
      st.pick = name;
      closePop();
      full.hidden = true;
      draw();
      renderRail();
      syncChrome();
    }

    /* ------------------------------------------------------------- canvas */

    var dragging = null;
    canvas.addEventListener('wheel', function (e) {
      // Same rule as the lineage canvas: a plain wheel belongs to the page.
      if (!e.ctrlKey && !e.metaKey) return;
      e.preventDefault();
      zoomAt(e.clientX, e.clientY, Math.exp(-e.deltaY * 0.002));
    }, { passive: false });
    canvas.addEventListener('pointerdown', function (e) {
      if (e.button !== 0 || e.target.closest('.dcvc-node')) return;
      dragging = { id: e.pointerId, x: e.clientX, y: e.clientY, moved: false };
      canvas.setPointerCapture(e.pointerId);
      canvas.classList.add('dragging');
    });
    canvas.addEventListener('pointermove', function (e) {
      if (!dragging || dragging.id !== e.pointerId) return;
      view.x += e.clientX - dragging.x;
      view.y += e.clientY - dragging.y;
      dragging.x = e.clientX; dragging.y = e.clientY; dragging.moved = true;
      applyTransform();
      if (popFor) placePop();
    });
    function endDrag(e) {
      if (!dragging || dragging.id !== e.pointerId) return;
      var moved = dragging.moved;
      dragging = null;
      canvas.classList.remove('dragging');
      if (!moved && !e.target.closest('.dcvc-node')) closePop();
    }
    canvas.addEventListener('pointerup', endDrag);
    canvas.addEventListener('pointercancel', endDrag);

    function applyTransform() {
      world.style.transform =
        'translate(' + view.x + 'px,' + view.y + 'px) scale(' + view.z + ')';
    }
    function zoomAt(cx, cy, f) {
      var r = canvas.getBoundingClientRect();
      var px = cx - r.left, py = cy - r.top;
      var next = clamp(view.z * f, MIN_ZOOM, MAX_ZOOM);
      var ratio = next / view.z;
      view.x = px - (px - view.x) * ratio;
      view.y = py - (py - view.y) * ratio;
      view.z = next;
      applyTransform();
    }
    var LEGIBLE = 0.72;

    function fit() {
      if (!layout || !layout.height) return;
      var r = canvas.getBoundingClientRect();
      if (!r.width) return;
      var z = Math.min(r.width / (layout.width * 1.1), r.height / (layout.height * 1.1));
      // A deep chain framed whole lands at z=0.5 and the labels stop being
      // labels. Below the legible floor, frame the WIDTH and start at the top:
      // the reader can pan down a chain, but cannot read one that has been
      // shrunk to fit.
      if (z < LEGIBLE) {
        view.z = clamp(Math.min(r.width / (layout.width * 1.06), LEGIBLE), MIN_ZOOM, MAX_ZOOM);
        view.x = (r.width - layout.width * view.z) / 2;
        view.y = 12;
      } else {
        view.z = clamp(z, MIN_ZOOM, 1.1);
        view.x = (r.width - layout.width * view.z) / 2;
        view.y = (r.height - layout.height * view.z) / 2;
      }
      applyTransform();
    }

    function layoutBands(items, bands) {
      var byBand = new Map(bands.map(function (b) { return [b, []]; }));
      items.forEach(function (d) { (byBand.get(d.layer) || []).push(d); });
      byBand.forEach(function (list) {
        list.sort(function (a, b) { return compareText(a.name, b.name); });
      });
      var widest = 1;
      byBand.forEach(function (l) { widest = Math.max(widest, l.length); });
      var W = PAD * 2 + widest * NODE_W + (widest - 1) * GAP_X;
      var nodes = {}, y = PAD;
      bands.forEach(function (b) {
        var list = byBand.get(b) || [];
        if (!list.length) return;
        var rowW = list.length * NODE_W + (list.length - 1) * GAP_X;
        var left = (W - rowW) / 2;
        list.forEach(function (d, i) {
          nodes[d.name] = { x: left + i * (NODE_W + GAP_X), y: y };
        });
        y += NODE_H + BAND_GAP;
      });
      return { nodes: nodes, width: W, height: y - BAND_GAP + PAD };
    }

    function draw() {
      var s = shown();
      clear(world);
      nodeEls = new Map(); edgeEls = [];
      empty.hidden = !!st.pick;
      if (!st.pick) { layout = null; return; }

      buildThemes();
      layout = layoutBands(s.items, s.bands);
      world.style.width = layout.width + 'px';
      world.style.height = layout.height + 'px';

      var svg = svgEl('svg', { class: 'dcvc-edges', width: layout.width,
        height: layout.height, viewBox: '0 0 ' + layout.width + ' ' + layout.height });
      world.appendChild(svg);

      var visible = new Set(s.items.map(function (d) { return d.name; }));
      s.items.forEach(function (d) {
        d.dependsOn.forEach(function (n) {
          if (!visible.has(n)) return;
          var from = layout.nodes[n], to = layout.nodes[d.name];
          if (!from || !to || from.y === to.y) return;
          var sx = from.x + NODE_W / 2, sy = from.y + NODE_H;
          var tx = to.x + NODE_W / 2, ty = to.y;
          var m = (sy + ty) / 2;
          var path = svgEl('path', { class: 'dcvc-edge', fill: 'none',
            stroke: dirColor('edge'), 'stroke-width': '1.6',
            d: 'M' + sx + ',' + sy + ' C' + sx + ',' + m + ' ' + tx + ',' + m +
              ' ' + tx + ',' + ty });
          svg.appendChild(path);
          edgeEls.push({ el: path, from: n, to: d.name });
        });
      });

      s.items.forEach(function (d) {
        var pos = layout.nodes[d.name];
        var theme = themeOf(d.folder);
        var isRoot = d.name === st.pick;
        var el = h('div', {
          class: 'dcvc-node' + (isRoot ? ' root' : ''),
          'data-name': d.name, tabindex: '0', role: 'button',
          'aria-label': d.name + ', ' + d.folder,
          style: {
            left: pos.x + 'px', top: pos.y + 'px',
            width: NODE_W + 'px', height: NODE_H + 'px',
            background: 'color-mix(in srgb, #ffffff ' + (st.dark ? 13 : 90) + '%, ' +
              theme.tint + ')',
            borderColor: isRoot ? dirColor('selected') : theme.border,
            boxShadow: isRoot ? '0 0 0 2px ' + dirColor('selected') : ''
          },
          onclick: function (e) { openPop(d, e.currentTarget); },
          onmouseenter: function () { highlight(d.name); },
          onmouseleave: function () { highlight(null); }
        }, [
          h('p', { class: 'dcvc-node-name', title: d.name, text: d.name }),
          h('p', { class: 'dcvc-node-sub' }, [
            h('span', { style: { color: theme.text }, text: d.folder }),
            d.dependsOn.length
              ? h('span', { class: 'dcvc-n', style: { color: dirColor('upstream') },
                  text: '↑' + d.dependsOn.length })
              : null,
            d.usedBy.length
              ? h('span', { class: 'dcvc-n', style: { color: dirColor('downstream') },
                  text: '↓' + d.usedBy.length })
              : null
          ])
        ]);
        nodeEls.set(d.name, el);
        world.appendChild(el);
      });

      applyTransform();
      fit();
    }

    function highlight(name) {
      edgeEls.forEach(function (e) {
        var on = name && (e.from === name || e.to === name);
        e.el.setAttribute('stroke', on
          ? dirColor(e.to === name ? 'upstream' : 'downstream')
          : dirColor('edge'));
        e.el.setAttribute('stroke-width', on ? '2.4' : '1.6');
      });
    }

    /* ------------------------------------------------------------ popover */

    function closePop() { pop.hidden = true; popFor = null; }

    function placePop() {
      if (!popFor) return;
      var el = nodeEls.get(popFor.name);
      if (!el) return closePop();
      var nr = el.getBoundingClientRect();
      var cr = canvas.getBoundingClientRect();
      var w = pop.offsetWidth || 320, hgt = pop.offsetHeight || 200;
      // Prefer the right of the node, flip left when that would overflow, and
      // clamp vertically so the peek never leaves the canvas it belongs to.
      var x = nr.right - cr.left + 12;
      if (x + w > cr.width - 8) x = nr.left - cr.left - w - 12;
      if (x < 8) x = 8;
      var y = nr.top - cr.top + nr.height / 2 - hgt / 2;
      y = Math.max(8, Math.min(y, cr.height - hgt - 8));
      pop.style.left = x + 'px';
      pop.style.top = y + 'px';
    }

    function fact(k, v) {
      return h('div', { class: 'dcvc-fact' }, [
        h('span', { class: 'dcvc-fact-k', text: k }),
        h('span', { class: 'dcvc-fact-v', text: v })
      ]);
    }

    function openPop(d, el) {
      popFor = d;
      full.hidden = true;
      clear(pop);
      var theme = themeOf(d.folder);
      // A peek carries three or four facts. More than that and it has outgrown
      // the variant and should have been the panel -- which is what the button
      // at the foot is for.
      var facts = (d.meta || []).slice(0, 3).map(function (m) { return fact(m[0], m[1]); });
      pop.appendChild(h('div', { class: 'dcvc-pop-head' }, [
        h('span', { class: 'dcvc-pop-chip',
          style: { background: theme.tint, color: theme.text, borderColor: theme.border },
          text: d.folder }),
        h('button', { type: 'button', class: 'dcvg-close', 'aria-label': 'Close',
          onclick: closePop }, ['×'])
      ]));
      pop.appendChild(h('p', { class: 'dcvc-pop-name', text: d.name }));
      facts.forEach(function (f) { pop.appendChild(f); });
      pop.appendChild(h('div', { class: 'dcvc-pop-refs' }, [
        h('span', { style: { color: dirColor('upstream') },
          text: '↑ ' + d.dependsOn.length + ' upstream' }),
        h('span', { style: { color: dirColor('downstream') },
          text: '↓ ' + d.usedBy.length + ' downstream' })
      ]));
      pop.appendChild(h('div', { class: 'dcvc-pop-actions' }, [
        h('button', { type: 'button', class: 'dcvc-pop-btn',
          onclick: function () { openFull(d); } }, ['Open full details']),
        d.name === st.pick ? null : h('button', {
          type: 'button', class: 'dcvc-pop-btn ghost',
          onclick: function () { pickMeasure(d.name); }
        }, ['Draw its chain'])
      ]));
      pop.hidden = false;
      placePop();
      highlight(d.name);
    }

    function openFull(d) {
      closePop();
      clear(full);
      var theme = themeOf(d.folder);
      var body = [
        h('div', { class: 'dcvg-detail-head' }, [
          h('span', { class: 'dcvg-folder-chip',
            style: { background: theme.tint, color: theme.text, borderColor: theme.border },
            text: (d.folderPath || []).join(' / ') }),
          h('button', { type: 'button', class: 'dcvg-close', 'aria-label': 'Close details',
            onclick: function () { full.hidden = true; } }, ['×'])
        ]),
        h('h4', { class: 'dcvg-detail-name', text: d.name })
      ];
      (d.meta || []).forEach(function (m) {
        body.push(h('p', { class: 'dcvg-detail-meta' }, [
          h('span', { class: 'dcvg-detail-key', text: m[0] }),
          h('span', { text: m[1] })
        ]));
      });
      if (d.description) {
        body.push(h('p', { class: 'dcvg-detail-desc', text: d.description }));
      }
      if (d.codeHtml) {
        body.push(h('p', { class: 'dcvg-section-title', text: 'DAX expression' }));
        var pre = h('pre', { class: 'dcvg-code' });
        pre.innerHTML = d.codeHtml;
        body.push(pre);
      }
      [['Depends on', d.dependsOn, 'upstream'], ['Used by', d.usedBy, 'downstream']]
        .forEach(function (sec) {
          body.push(h('p', { class: 'dcvg-section-title', text: sec[0] }));
          body.push(sec[1].length
            ? h('div', { class: 'dcvg-chips' }, sec[1].map(function (n) {
                return h('button', {
                  type: 'button', class: 'dcvg-ref', text: n,
                  style: { borderColor: dirColor(sec[2]) + '55',
                    backgroundColor: dirColor(sec[2]) + '14' },
                  onclick: function () { pickMeasure(n); }
                });
              }))
            : h('p', { class: 'dcvg-section-empty', text: 'None.' }));
        });
      full.appendChild(h('div', { class: 'dcvg-detail-body' }, body));
      full.hidden = false;
    }

    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && !pop.hidden) closePop();
    });

    var obs = new MutationObserver(function () {
      var next = isDark();
      if (next === st.dark) return;
      st.dark = next;
      renderRail();
      draw();
    });
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] });
    obs.observe(document.body, { attributes: true, attributeFilter: ['class'] });

    buildThemes();
    renderRail();
    syncChrome();
    /**
     * Open on the measure with the DEEPEST upstream chain, not the most-read one.
     * Most-read is the obvious pick and it is wrong here: the measure everything
     * depends on is usually a bare SUM with no ancestors at all, so the default
     * Upstream view opened on a single node and looked broken. The deepest chain
     * is the one that shows what this view is for.
     */
    var seed = ALL.slice().sort(function (a, b) {
      return chainOf(b.name, 'up').size - chainOf(a.name, 'up').size ||
        b.usedBy.length - a.usedBy.length || compareText(a.name, b.name);
    })[0];
    if (seed) {
      if (window.ResizeObserver) {
        var ro = new ResizeObserver(function () {
          if (canvas.getBoundingClientRect().width > 0) {
            pickMeasure(seed.name);
            ro.disconnect();
          }
        });
        ro.observe(canvas);
      } else {
        setTimeout(function () { pickMeasure(seed.name); }, 60);
      }
    }
  }

  window.dcvChain = function (mountId, dataId) {
    var mount = document.getElementById(mountId);
    var raw = document.getElementById(dataId);
    if (!mount || !raw) return;
    makeChain(mount, JSON.parse(raw.textContent));
  };
})();
