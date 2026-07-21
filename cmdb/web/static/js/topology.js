/* Topology visualizer: renders /topology/data with Cytoscape, styled from the
   CSS custom properties so it follows the light/dark theme.

   Readability model (ui/topology "1a"):
     - The graph is a compound hierarchy (host > compose > container and
       cluster > namespace > workload). It loads COLLAPSED to a skeleton of
       hosts + clusters; double-click a group (or use its cue) to drill in.
     - Images & vulns live off-canvas: worst severity is a ring on the node
       (sev-* classes from the service) and CVE counts show in the panel.
       A collapsed group with a hidden critical carries `has-crit` -> red ring.
     - A filter bar (search / host-cluster / critical-only) narrows the view;
       any active filter auto-expands so matches deep in a group are reachable,
       and clearing every filter collapses back to the skeleton.
     - Layer toggles, Re-layout and the theme toggle behave as before. */
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
    var ecApi = null;      // expand-collapse api
    var expanded = false;  // are we currently expanded for a filter?

    function token(name) {
      return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    }

    function buildStyle() {
      var accent = token('--accent');
      var accentHover = token('--accent-hover');
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
            'background-opacity': 0.45,
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
        // Cluster is now a compound container, not a lone hexagon.
        { selector: 'node.cluster', style: {
            'border-color': accent, 'border-width': 1.5,
            'background-opacity': 0.35, 'color': textMain,
            'font-size': 11, 'font-weight': 600
        }},
        { selector: 'node.k8s-namespace', style: {
            'border-style': 'dashed', 'border-color': accent, 'color': textSubtle
        }},
        { selector: 'node.compose', style: {
            'border-style': 'dashed', 'border-color': textSubtle, 'color': textSubtle
        }},
        { selector: 'node.k8s-workload', style: {
            'shape': 'round-rectangle', 'width': 16, 'height': 16,
            'border-color': textSubtle
        }},
        { selector: 'node.subnet, node.tailnet', style: {
            'shape': 'ellipse', 'width': 26, 'height': 26,
            'background-opacity': 0.15,
            'border-style': 'dashed',
            'border-color': textSubtle
        }},
        { selector: 'node.container', style: { 'width': 16, 'height': 16, 'border-color': textSubtle } },
        { selector: 'node.container.state-exited, node.container.state-dead', style: {
            'background-color': critical, 'background-opacity': 0.55
        }},
        // Severity rings (leaves + collapsed groups via has-crit).
        { selector: 'node.sev-high', style: { 'border-width': 2, 'border-color': accent } },
        { selector: 'node.sev-critical', style: { 'border-width': 2.5, 'border-color': critical } },
        { selector: 'node.has-crit', style: { 'border-color': critical } },
        // Legacy image nodes (layer-images, off by default).
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
        // Filter states.
        { selector: 'node.dim, edge.dim', style: { 'opacity': 0.1 } },
        { selector: 'node.match', style: { 'border-width': 3, 'border-color': accentHover } },
        { selector: '.hidden-layer', style: { 'display': 'none' } },
        { selector: 'node:selected', style: { 'border-width': 3, 'border-color': accent } }
      ];
    }

    function layoutOpts() {
      var haveFcose = typeof cytoscapeFcose !== 'undefined';
      return {
        name: haveFcose ? 'fcose' : 'cose',
        animate: false,
        padding: 30,
        randomize: true,
        quality: 'default',
        nodeRepulsion: 12000,
        idealEdgeLength: 80,
        nestingFactor: 0.85,
        tile: true,
        packComponents: true
      };
    }

    function runLayout() {
      cy.layout(layoutOpts()).run();
      cy.fit(undefined, 40);
    }

    /* ── detail panel ── */
    var panel = document.getElementById('topology-panel');
    var panelTitle = document.getElementById('panel-title');
    var panelBody = document.getElementById('panel-body');
    var panelOpen = document.getElementById('panel-open');

    function esc(s) {
      var d = document.createElement('div');
      d.textContent = s == null ? '' : String(s);
      return d.innerHTML;
    }

    function vulnChips(v) {
      if (!v) return '<p class="panel-addr">not scanned</p>';
      var chip = function (n, label, cls) {
        return '<span class="vuln-chip ' + cls + '">' + n + ' ' + label + '</span>';
      };
      return '<div class="vuln-chips">' +
        chip(v.critical, 'C', 'vc-critical') +
        chip(v.high, 'H', 'vc-high') +
        chip(v.medium, 'M', 'vc-medium') +
        chip(v.low, 'L', 'vc-low') + '</div>';
    }

    function showPanel(data) {
      panelTitle.textContent = data.label || data.id;
      var rows = [['kind', data.kind]];
      if (data.ip) rows.push(['ip', data.ip]);
      if (data.os) rows.push(['os', data.os]);
      if (data.cluster) rows.push(['cluster', data.cluster]);
      if (data.namespace) rows.push(['namespace', data.namespace]);
      if (data.replicas) rows.push(['replicas', data.replicas]);
      if (data.count) rows.push(['members', data.count]);
      if (data.state) rows.push(['state', data.state]);
      if (data.online != null) rows.push(['tailscale', data.online ? 'online' : 'offline']);
      var html = '<table>' + rows.map(function (r) {
        return '<tr><th>' + esc(r[0]) + '</th><td>' + esc(r[1]) + '</td></tr>';
      }).join('') + '</table>';

      if (data.image) {
        html += '<h3>Image</h3><p><code>' + esc(data.image) + '</code></p>';
        html += vulnChips(data.vulns);
      }
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

    /* ── layers ── */
    function applyLayers() {
      if (!cy) return;
      cy.batch(function () {
        cy.elements().removeClass('hidden-layer');
        document.querySelectorAll('.layer-toggles input[data-layer]').forEach(function (box) {
          var layer = box.getAttribute('data-layer');
          if (layer === 'layer-exposure') {
            cy.nodes('.host').forEach(function (n) {
              var expose = box.checked && (n.data('exposed_ports') > 0 || n.data('funnel'));
              n.toggleClass('exposed', expose);
              n.toggleClass('funnel', box.checked && !!n.data('funnel'));
            });
            return;
          }
          if (!box.checked) cy.elements('.' + layer).addClass('hidden-layer');
        });
      });
    }

    /* ── filters ── */
    var searchInput = document.getElementById('topo-search');
    var scopeSelect = document.getElementById('topo-scope');
    var critToggle = document.getElementById('topo-crit');
    var fstate = { q: '', scope: '', crit: false };

    function filtersActive() { return fstate.q || fstate.scope || fstate.crit; }

    function populateScope() {
      if (!scopeSelect) return;
      cy.nodes('.host, .cluster').forEach(function (n) {
        var opt = document.createElement('option');
        opt.value = n.id();
        var name = (n.data('label') || '').replace(/ ·\d+$/, '');
        opt.textContent = (n.data('kind') === 'k8s_cluster' ? 'cluster · ' : 'host · ') + name;
        scopeSelect.appendChild(opt);
      });
    }

    function applyFilters() {
      if (!cy) return;
      if (filtersActive() && ecApi && !expanded) { ecApi.expandAll(); expanded = true; }
      cy.batch(function () {
        cy.elements().removeClass('dim match');
        if (!filtersActive()) return;
        var keep = cy.collection();
        var seed = cy.nodes(':visible');
        if (fstate.scope) {
          var scope = cy.getElementById(fstate.scope);
          seed = seed.intersection(scope.union(scope.descendants()));
        }
        if (fstate.crit) seed = seed.filter('.sev-critical');
        if (fstate.q) {
          var q = fstate.q.toLowerCase();
          var m = seed.filter(function (n) {
            return (n.data('label') || '').toLowerCase().indexOf(q) > -1;
          });
          m.addClass('match');
          seed = m;
        }
        seed.forEach(function (n) { keep = keep.union(n).union(n.ancestors()); });
        // Show a group's members too — except in critical-only, where we want
        // just the path to each critical leaf.
        if (!fstate.crit) seed.forEach(function (n) { keep = keep.union(n.descendants()); });
        if (keep.length) {
          cy.nodes(':visible').difference(keep).addClass('dim');
          cy.edges().addClass('dim');
          keep.edgesWith(keep).removeClass('dim');
        }
      });
    }

    function collapseIfCleared() {
      if (!filtersActive() && ecApi && expanded) {
        ecApi.collapseAll();
        expanded = false;
        cy.fit(undefined, 40);
      }
    }

    /* ── boot ── */
    fetch(container.getAttribute('data-endpoint'))
      .then(function (r) { return r.json(); })
      .then(function (graph) {
        cy = cytoscape({
          container: container,
          elements: { nodes: graph.nodes, edges: graph.edges },
          style: buildStyle(),
          wheelSensitivity: 0.2
        });

        if (typeof cy.expandCollapse === 'function') {
          ecApi = cy.expandCollapse({
            layoutBy: layoutOpts(),
            fisheye: false,
            animate: false,
            undoable: false,
            cueEnabled: true,
            expandCollapseCuePosition: 'top-left',
            expandCollapseCueSize: 14
          });
        }

        runLayout();
        applyLayers();
        populateScope();

        // Default: collapse everything to the hosts + clusters skeleton.
        if (ecApi) { ecApi.collapseAll(); expanded = false; cy.fit(undefined, 40); }

        cy.on('tap', 'node', function (evt) { showPanel(evt.target.data()); });
        cy.on('tap', function (evt) { if (evt.target === cy) panel.hidden = true; });
      })
      .catch(function (err) {
        container.innerHTML = '<p class="chart-placeholder">Failed to load topology: ' + esc(err) + '</p>';
      });

    /* ── controls ── */
    document.querySelectorAll('.layer-toggles input[data-layer]').forEach(function (box) {
      box.addEventListener('change', function () { applyLayers(); applyFilters(); });
    });

    if (searchInput) searchInput.addEventListener('input', function () {
      fstate.q = searchInput.value.trim(); applyFilters(); collapseIfCleared();
    });
    if (scopeSelect) scopeSelect.addEventListener('change', function () {
      fstate.scope = scopeSelect.value; applyFilters(); collapseIfCleared();
    });
    if (critToggle) critToggle.addEventListener('change', function () {
      fstate.crit = critToggle.checked; applyFilters(); collapseIfCleared();
    });

    var expandBtn = document.getElementById('expand-all');
    if (expandBtn) expandBtn.addEventListener('click', function () {
      if (ecApi) { ecApi.expandAll(); expanded = true; cy.fit(undefined, 40); }
    });
    var collapseBtn = document.getElementById('collapse-all');
    if (collapseBtn) collapseBtn.addEventListener('click', function () {
      if (ecApi) { ecApi.collapseAll(); expanded = false; cy.fit(undefined, 40); }
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
