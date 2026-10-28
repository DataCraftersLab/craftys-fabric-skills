/**
 * dcv-tabular -- a view switcher over grouped, collapsible tables.
 *
 * Two cards want the same three things: pick a grain, read a stat stripe, then walk a
 * table that is grouped the way the model groups it. So one widget, two mounts, and the
 * difference between them is entirely payload.
 *
 * The switcher is a ghost tab strip: no track, no rule,
 * no fill. The active view is green and carries a dot, and the dot is not decoration --
 * without it the only difference between selected and not is a colour and a weight,
 * which is not enough on a projector or for a colourblind reader. Keyboard behaviour is
 * the usual tablist pattern: role=tablist, arrows move and select, Home and End jump.
 *
 * Grouping is per grain and not a display preference: measures group by display folder,
 * columns group by table. A flat list of 19 columns from 6 tables is not a list anyone
 * can audit, because the question is always "what does THIS table look like".
 */
(function () {
  'use strict';

  var S = window.dcvShared;
  var h = S.h, clear = S.clear, PALETTE = S.PALETTE, NEUTRAL = S.NEUTRAL, isDark = S.isDark;

  window.dcvTabular = function (mountId, dataId) {
    var mount = document.getElementById(mountId);
    var data = JSON.parse(document.getElementById(dataId).textContent);
    if (!mount || !data.views || !data.views.length) return;

    var st = { view: data.views[0].id, dark: isDark(), open: Object.create(null) };

    /* Group colour comes from the canvas palette, keyed on the group's ordinal within
       its own view, so a folder wears the same colour here as it does in the graph. */
    function toneOf(view, i) {
      var p = PALETTE[i % PALETTE.length][st.dark ? 'dark' : 'light'];
      return view.flatTone ? (st.dark ? NEUTRAL.dark : NEUTRAL.light) : p;
    }

    var root = h('div', { class: 'dcvt' });
    var tablist = h('div', { class: 'dcvt-views', role: 'tablist', 'aria-label': 'Grain' });
    var body = h('div', { class: 'dcvt-body' });
    var tools = h('div', { class: 'dcvt-tools' });
    var bar = h('div', { class: 'dcvt-bar' }, [tablist, tools]);
    root.appendChild(bar);
    root.appendChild(body);
    mount.appendChild(root);

    /* -- the switcher --------------------------------------------------------- */

    var tabs = [];
    data.views.forEach(function (v) {
      var btn = h('button', {
        type: 'button', class: 'dcvt-view', role: 'tab', id: mountId + '-tab-' + v.id,
        'data-view': v.id, title: v.hint || '',
        onclick: function () { pick(v.id); }
      }, [h('span', { class: 'dcvt-view-dot' }), h('span', { text: v.label })]);
      tabs.push(btn);
      tablist.appendChild(btn);
    });

    tablist.addEventListener('keydown', function (e) {
      var i = data.views.findIndex(function (v) { return v.id === st.view; });
      var n = null;
      if (e.key === 'ArrowRight' || e.key === 'ArrowDown') n = (i + 1) % tabs.length;
      else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') n = (i - 1 + tabs.length) % tabs.length;
      else if (e.key === 'Home') n = 0;
      else if (e.key === 'End') n = tabs.length - 1;
      if (n === null) return;
      e.preventDefault();
      pick(data.views[n].id);
      tabs[n].focus();
    });

    function pick(id) {
      if (st.view === id) return;
      st.view = id;
      render();
    }

    /* -- rendering ------------------------------------------------------------ */

    function viewOf(id) {
      return data.views.filter(function (v) { return v.id === id; })[0];
    }

    function cellHtml(col, row) {
      var v = row[col.key];
      if (v === null || v === undefined || v === '') {
        return h('span', { class: 'dcvt-blank', text: col.blank || '—' });
      }
      if (col.kind === 'level') {
        return h('span', { class: 'dcvt-level ' + v.tone, text: v.label });
      }
      if (col.kind === 'chip') {
        return h('span', { class: 'dcvt-word', style: { borderColor: v.tone, color: v.tone },
                           text: v.label });
      }
      if (col.kind === 'flags') {
        if (!v.length) return h('span', { class: 'dcvt-blank', text: '—' });
        return h('span', { class: 'dcvt-flags' }, v.map(function (f) {
          return h('i', { class: 'dcvt-flag' + (f.sym === ',' ? ' comma' : ''),
                          title: f.hint || '' }, [h('span', { text: f.sym })]);
        }));
      }
      if (col.kind === 'num') {
        return h('span', { class: 'dcvt-num', text: String(v) });
      }
      if (col.kind === 'code') {
        return h('code', { class: 'dcvt-code', title: v, text: v });
      }
      if (col.kind === 'prose') {
        if (v.indexOf('`') === -1) return h('span', { class: 'dcvt-prose', text: v });
        var wrap = h('span', { class: 'dcvt-prose' });
        v.split('`').forEach(function (part, i) {
          if (!part) return;
          wrap.appendChild(i % 2
            ? h('code', { class: 'dcvt-code-inline', text: part })
            : document.createTextNode(part));
        });
        return wrap;
      }
      return h('span', { class: 'dcvt-name', title: String(v), text: String(v) });
    }

    function groupTable(view, g) {
      function cls(c) {
        return (c.kind === 'num' ? 'dcvt-r ' : '') + (c.grow ? 'dcvt-grow' : '');
      }
      /* Every group is its own <table>, so with auto layout each one sized its columns
         to its own longest cell and no two groups lined up. Fixed layout plus one
         colgroup puts all of them on the same grid; the single column with no width
         declared absorbs whatever is left. */
      var cg = h('colgroup', {}, view.columns.map(function (c) {
        return h('col', c.w ? { style: { width: c.w + 'px' } } : {});
      }));
      var head = h('tr', {}, view.columns.map(function (c) {
        return h('th', { class: cls(c), text: c.label });
      }));
      var rows = g.rows.map(function (r) {
        return h('tr', {}, view.columns.map(function (c) {
          return h('td', { class: cls(c) }, [cellHtml(c, r)]);
        }));
      });
      var fixed = view.columns.reduce(function (n, c) { return n + (c.w || 0); }, 0);
      return h('div', { class: 'dcvt-wrap' }, [
        h('table', { class: 'dcvt-table', style: { minWidth: (fixed + 260) + 'px' } }, [
          cg, h('thead', {}, [head]), h('tbody', {}, rows)
        ])
      ]);
    }

    function render() {
      tabs.forEach(function (b) {
        var on = b.getAttribute('data-view') === st.view;
        b.classList.toggle('on', on);
        b.setAttribute('aria-selected', on ? 'true' : 'false');
        b.setAttribute('tabindex', on ? '0' : '-1');
      });

      var view = viewOf(st.view);
      clear(body);
      clear(tools);

      if (view.groups.length > 1) {
        tools.appendChild(h('button', {
          type: 'button', class: 'dcvt-all', text: 'Expand all',
          onclick: function () { setAll(true); }
        }));
        tools.appendChild(h('button', {
          type: 'button', class: 'dcvt-all', text: 'Collapse all',
          onclick: function () { setAll(false); }
        }));
      }

      if (view.stats && view.stats.length) {
        body.appendChild(h('div', { class: 'dcvt-strip' }, view.stats.map(function (s) {
          return h('div', { class: 'dcvt-stat' + (s.tone ? ' ' + s.tone : '') }, [
            h('span', { class: 'dcvt-stat-v', text: String(s.value) }),
            h('span', { class: 'dcvt-stat-l', text: s.label }),
            s.sub ? h('span', { class: 'dcvt-stat-s', text: s.sub }) : null
          ]);
        })));
      }
      if (view.note) {
        body.appendChild(h('p', { class: 'dcvt-note', text: view.note }));
      }

      var wrap = h('div', { class: 'dcvt-groups' });
      view.groups.forEach(function (g, i) {
        var tone = toneOf(view, i);
        var key = st.view + '::' + g.key;
        if (st.open[key] === undefined) st.open[key] = i === 0 && view.groups.length > 1;
        if (view.groups.length === 1) st.open[key] = true;

        if (view.groups.length === 1) {
          wrap.appendChild(groupTable(view, g));
          return;
        }
        var d = h('details', { class: 'dcvt-group' }, [
          h('summary', {}, [
            h('span', { class: 'dcvt-dot', style: { background: tone.dot } }),
            h('span', { class: 'dcvt-g-name', text: g.label }),
            g.meta ? h('span', { class: 'dcvt-g-meta', text: g.meta }) : null,
            h('span', { class: 'dcvt-g-n' + (g.nTone ? ' ' + g.nTone : ''),
                        title: g.nHint || '', text: g.n || String(g.rows.length) })
          ]),
          g.help ? h('p', { class: 'dcvt-help', text: g.help }) : null,
          groupTable(view, g)
        ]);
        d.open = st.open[key];
        d.addEventListener('toggle', function () { st.open[key] = d.open; });
        wrap.appendChild(d);
      });
      body.appendChild(wrap);
    }

    function setAll(open) {
      var view = viewOf(st.view);
      view.groups.forEach(function (g) { st.open[st.view + '::' + g.key] = open; });
      render();
    }

    var theme = document.querySelector('[data-dc-theme]');
    if (theme) {
      theme.addEventListener('click', function () {
        setTimeout(function () { st.dark = isDark(); render(); }, 30);
      });
    }

    render();
  };
})();
