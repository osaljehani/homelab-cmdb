/* Topology visualizer: renders /topology/data with Cytoscape, styled from the
   CSS custom properties so it follows the light/dark theme. Layers toggle
   client-side; exposure is a ring on host nodes rather than extra elements. */
(function () {
  'use strict';

  function ready(fn) {
    if (document.readyState !== 'loading') fn();
    else document.addEventListener('DOMContentLoaded', fn);
  }

  ready(function () {
    var container = document.getElementById('cy');
    if (!container || typeof cytoscape === 'undefined') return;

    var cy = null;

    function token(name) {
      return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    }

    function buildStyle() {
      var accent = token('--accent');
      var border = token('--border');
      var surface = token('--bg-surface');
      var surfaceHover = token('--bg-surface-hover');
      var textMain = token('--text-main');
      var textMuted = token('--text-muted');
      var textSubtle = token('--text-subtle');
      var critical = token('--status-critical');
      var ok = token('--status-ok');
      var mono = token('--font-mono') || 'monospace';

      return [
        { selector: 'node', style: {
            'label': 'data(label)',
            'font-family': mono,
            'font-size': 9,
            'color': textMuted,
            'text-valign': 'bottom',
            'text-margin-y': 4,
            'background-color': surfaceHover,
            'border-width': 1,
            'border-color': border,
            'width': 22, 'height': 22,
            'text-wrap': 'ellipsis',
            'text-max-width': 110
        }},
        { selector: ':parent', style: {
            'background-color': surface,
            'background-opacity': 0.5,
            'border-color': border,
            'border-width': 1,
            'shape': 'round-rectangle',
            'text-valign': 'top',
            'text-margin-y': -4,
            'font-size': 10,
            'color': textSubtle,
            'padding': 12
        }},
        { selector: 'node.host', style: {
            'shape': 'round-rectangle',
            'width': 34, 'height': 34,
            'background-color': surface,
            'border-color': textSubtle,
            'color': textMain,
            'font-size': 11,
            'font-weight': 600
        }},
        { selector: 'node.host.exposed', style: { 'border-width': 2, 'border-color': accent } },
        { selector: 'node.host.funnel', style: { 'border-width': 3, 'border-color': critical } },
        { selector: 'node.host.exit-node', style: { 'shape': 'diamond' } },
        { selector: 'node.k8s', style: {
            'shape': 'hexagon', 'width': 30, 'height': 30,
            'border-color': accent, 'color': textMain
        }},
        { selector: 'node.subnet, node.tailnet', style: {
            'shape': 'ellipse', 'width': 26, 'height': 26,
            'background-opacity': 0.15,
            'border-style': 'dashed',
            'border-color': textSubtle
        }},
        { selector: 'node.container', style: { 'width': 16, 'height': 16 } },
        { selector: 'node.container.state-exited, node.container.state-dead', style: {
            'background-color': critical, 'background-opacity': 0.55
        }},
        { selector: 'node.image', style: {
            'shape': 'round-tag', 'width': 18, 'height': 18,
            'border-color': textSubtle, 'color': textSubtle, 'font-size': 8
        }},
        { selector: 'node.image.sev-critical', style: { 'border-width': 2, 'border-color': critical, 'color': critical } },
        { selector: 'node.image.sev-high', style: { 'border-width': 2, 'border-color': accent } },
        { selector: 'node.image.sev-clean', style: { 'border-color': ok } },
        { selector: 'edge', style: {
            'width': 1.5, 'line-color': border,
            'curve-style': 'bezier',
            'target-arrow-shape': 'none'
        }},
        { selector: 'edge.tailscale', style: { 'line-style': 'dashed', 'line-color': textSubtle } },
        { selector: 'edge.ts-offline', style: { 'line-opacity': 0.4 } },
        { selector: 'edge.k8s-member', style: {
            'line-color': accent, 'line-opacity': 0.6,
            'label': 'data(role)', 'font-size': 8, 'font-family': mono,
            'color': textSubtle, 'text-rotation': 'autorotate', 'text-margin-y': -6
        }},
        { selector: 'edge.runs', style: { 'line-opacity': 0.5 } },
        { selector: 'edge.runs.sev-critical', style: { 'line-color': critical, 'line-opacity': 0.9, 'width': 2 } },
        { selector: 'edge.runs.sev-high', style: { 'line-color': accent, 'line-opacity': 0.8 } },
        { selector: '.hidden-layer', style: { 'display': 'none' } },
        { selector: 'node:selected', style: { 'border-width': 3, 'border-color': accent } }
      ];
    }

    function runLayout() {
      // fcose handles compound nesting well; fall back to the built-in cose
      // if the extension failed to load for any reason.
      var haveFcose = typeof cytoscapeFcose !== 'undefined';
      cy.layout({
        name: haveFcose ? 'fcose' : 'cose',
        animate: false,
        padding: 30,
        randomize: true,
        nodeRepulsion: 12000,
        idealEdgeLength: 80,
        nestingFactor: 0.8
      }).run();
      cy.fit(undefined, 40);
    }

    var panel = document.getElementById('topology-panel');
    var panelTitle = document.getElementById('panel-title');
    var panelBody = document.getElementById('panel-body');
    var panelOpen = document.getElementById('panel-open');

    function esc(s) {
      var d = document.createElement('div');
      d.textContent = s == null ? '' : String(s);
      return d.innerHTML;
    }

    function showPanel(data) {
      panelTitle.textContent = data.label || data.id;
      var rows = [['kind', data.kind]];
      if (data.ip) rows.push(['ip', data.ip]);
      if (data.os) rows.push(['os', data.os]);
      if (data.state) rows.push(['state', data.state]);
      if (data.image) rows.push(['image', data.image]);
      if (data.online != null) rows.push(['tailscale', data.online ? 'online' : 'offline']);
      var html = '<table>' + rows.map(function (r) {
        return '<tr><th>' + esc(r[0]) + '</th><td>' + esc(r[1]) + '</td></tr>';
      }).join('') + '</table>';

      if (data.ports && data.ports.length) {
        html += '<h3>Listening ports</h3><ul class="panel-list">' + data.ports.map(function (p) {
          return '<li><code>' + esc(p.proto) + '/' + esc(p.port) + '</code> ' + esc(p.process || '') +
                 ' <span class="panel-addr">' + esc(p.address || '') + '</span></li>';
        }).join('') + '</ul>';
      }
      if (data.services && data.services.length) {
        html += '<h3>Tailscale services</h3><ul class="panel-list">' + data.services.map(function (s) {
          return '<li><code>' + esc(s.proto) + '/' + esc(s.port) + '</code> → ' + esc(s.target || '') +
                 (s.funnel ? ' <b class="panel-funnel">funnel</b>' : '') + '</li>';
        }).join('') + '</ul>';
      }
      panelBody.innerHTML = html;
      if (data.url) {
        panelOpen.href = data.url;
        panelOpen.hidden = false;
      } else {
        panelOpen.hidden = true;
      }
      panel.hidden = false;
    }

    document.getElementById('panel-close').addEventListener('click', function () {
      panel.hidden = true;
      if (cy) cy.$(':selected').unselect();
    });

    function applyLayers() {
      if (!cy) return;
      cy.batch(function () {
        document.querySelectorAll('.layer-toggles input[data-layer]').forEach(function (box) {
          var layer = box.getAttribute('data-layer');
          if (layer === 'layer-exposure') {
            // Exposure is a ring on host nodes, not separate elements.
            cy.nodes('.host').forEach(function (n) {
              var expose = box.checked && (n.data('exposed_ports') > 0 || n.data('funnel'));
              n.toggleClass('exposed', expose);
              n.toggleClass('funnel', box.checked && !!n.data('funnel'));
            });
            return;
          }
          var els = cy.elements('.' + layer);
          // Never hide infra-layer compounds that still have visible children.
          els.toggleClass('hidden-layer', !box.checked);
        });
      });
    }

    fetch(container.getAttribute('data-endpoint'))
      .then(function (r) { return r.json(); })
      .then(function (graph) {
        cy = cytoscape({
          container: container,
          elements: { nodes: graph.nodes, edges: graph.edges },
          style: buildStyle(),
          wheelSensitivity: 0.2
        });
        runLayout();
        applyLayers();

        cy.on('tap', 'node', function (evt) {
          showPanel(evt.target.data());
        });
        cy.on('tap', function (evt) {
          if (evt.target === cy) panel.hidden = true;
        });
      })
      .catch(function (err) {
        container.innerHTML = '<p class="chart-placeholder">Failed to load topology: ' + esc(err) + '</p>';
      });

    document.querySelectorAll('.layer-toggles input[data-layer]').forEach(function (box) {
      box.addEventListener('change', applyLayers);
    });

    document.getElementById('relayout').addEventListener('click', function () {
      if (cy) runLayout();
    });

    var themeToggle = document.getElementById('theme-toggle');
    if (themeToggle) {
      themeToggle.addEventListener('click', function () {
        if (cy) cy.style(buildStyle());
      });
    }
  });
})();
