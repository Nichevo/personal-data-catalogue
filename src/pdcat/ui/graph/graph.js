// The catalogue's graph viewer: a Streamlit component (no build step) drawing
// code maps and ERDs with Cytoscape, laid out by ELK, with groups that open
// and close. It talks to Streamlit with the plain postMessage protocol.
(function () {
  "use strict";
  cytoscape.use(cytoscapeElk);
  cytoscape.use(cytoscapeExpandCollapse);

  const COLORS = {
    code: ["#dbe8ff", "#3b6fd8"], library: ["#ece2ff", "#7a4fd1"], project: ["#dbe8ff", "#3b6fd8"],
    table: ["#dcf3e3", "#2f9e57"], view: ["#e6f6ea", "#4caf6f"], file: ["#fff5cc", "#c9a227"],
    package: ["#eeeeee", "#8a8a8a"], report: ["#ffe7d6", "#e07b39"], workbook: ["#ddf1e6", "#1e7a46"],
    function: ["#eef3ff", "#5b7fd6"], other: ["#f1f1f1", "#999999"],
  };
  const EDGE = { imports: "#3b6fd8", runs: "#8a4ac9", reads: "#7a8699", writes: "#d64545", calls: "#5b7fd6", declared: "#2f9e57",
                 confirmed: "#2f9e57", suggested: "#b0b6c3", uses: "#7a8699" };

  let cy = null, api = null, args = {}, lastData = null, clickTimer = null, sent = 0;

  function send(type, extra) {
    window.parent.postMessage(Object.assign({ isStreamlitMessage: true }, { type: type }, extra || {}), "*");
  }
  function setValue(v) {
    sent += 1;
    send("streamlit:setComponentValue", { value: Object.assign({ n: sent, at: Date.now() }, v), dataType: "json" });
  }

  const ruler = document.createElement("canvas").getContext("2d");
  function tagWidth(n) {
    ruler.font = '11px "Source Sans Pro", "Segoe UI", sans-serif';
    const longest = Math.max(...String(n.data("label") || "").split("\n").map((t) => ruler.measureText(t).width));
    return Math.min(longest, 150) * 1.35 + 10;
  }

  function style(dark) {
    const fg = dark ? "#e8e8ea" : "#262730";
    const groupBg = dark ? "#23252c" : "#f6f7f9";
    const s = [
      { selector: "node", style: {
          "label": "data(label)", "font-size": 11, "color": fg, "text-valign": "center", "text-halign": "center",
          "text-wrap": "wrap", "text-max-width": 150, "shape": "round-rectangle", "padding": "6px",
          "width": "label", "height": "label", "border-width": 1.5 } },
      { selector: "node:parent, node.cy-expand-collapse-collapsed-node", style: {
          "background-color": groupBg, "background-opacity": 0.6, "border-color": dark ? "#555" : "#c5c8d0",
          "border-style": "dashed", "text-valign": "top", "text-halign": "center", "font-weight": "bold",
          "font-size": 12, "padding": "14px", "shape": "round-rectangle" } },
      { selector: "node.cy-expand-collapse-collapsed-node", style: {
          "border-style": "solid", "label": (n) => n.data("label") + "  ▸ " + (n.data("count") || ""),
          "text-valign": "center", "padding": "10px", "width": "label", "height": "label" } },
      { selector: "edge", style: {
          "curve-style": "bezier", "target-arrow-shape": "triangle", "width": 1.4, "arrow-scale": 0.9,
          "line-color": (e) => EDGE[e.data("relation")] || "#999",
          "target-arrow-color": (e) => EDGE[e.data("relation")] || "#999",
          "label": "data(label)", "font-size": 9, "color": dark ? "#aaa" : "#555", "text-rotation": "autorotate",
          "text-background-color": dark ? "#0e1117" : "#ffffff", "text-background-opacity": 0.8,
          "text-background-padding": 1 } },
      { selector: "edge[relation = 'reads'], edge[relation = 'suggested'], edge[relation = 'uses']",
        style: { "line-style": "dashed" } },
      { selector: "edge[?meta]", style: { "width": 2.2, "label": "" } },
      { selector: ".faded", style: { "opacity": 0.18 } },
      { selector: "edge.dup", style: { "display": "none" } },
      { selector: "edge[bundle > 1]", style: { "width": (e) => Math.min(1.4 + e.data("bundle") * 0.5, 6) } },
      { selector: "node:selected", style: { "border-width": 3, "border-color": "#ff4b4b" } },
      { selector: "edge:selected", style: { "width": 3 } },
    ];
    Object.keys(COLORS).forEach((k) => {
      s.splice(1, 0, { selector: `node[kind = '${k}']`, style: { "background-color": COLORS[k][0],
                                                                  "border-color": COLORS[k][1] } });
    });
    s.splice(1, 0, { selector: "node[kind = 'table'], node[kind = 'view']", style: { "shape": "barrel" } });
    // A tag's point takes the right third of the box, so make it wider and move the text left.
    s.splice(1, 0, { selector: "node[kind = 'file']", style: { "shape": "tag", "width": tagWidth,
                                                                "text-margin-x": (n) => -0.12 * tagWidth(n) } });
    s.splice(1, 0, { selector: "node[kind = 'package']", style: { "shape": "rectangle" } });
    return s;
  }

  // Packed: groups are laid out on their own and then packed into a box shaped like the viewer, for
  // groups that don't flow into each other (database subject areas). Laid out together, ELK puts
  // unconnected groups in one long row.
  const GROUP = { "algorithm": "layered", "elk.separateConnectedComponents": true, "elk.aspectRatio": 1.6,
                  "elk.spacing.nodeNode": 28, "elk.layered.spacing.nodeNodeBetweenLayers": 50,
                  "elk.padding": "[top=28,left=12,bottom=12,right=12]" };

  function viewerShape() {
    const el = document.getElementById("cy");
    return el.clientHeight ? el.clientWidth / el.clientHeight : 1.6;
  }

  // Room between columns of boxes for the longest line label ("PlantId = PlantId (inner)").
  function betweenLayers() {
    const longest = Math.max(0, ...(args.elements || []).filter((e) => e.data.source)
      .map((e) => String(e.data.label || "").length));
    return Math.max(60, Math.min(longest * 5.5 + 30, 260));
  }

  function layoutOptions(fit) {
    if (args.packed) {
      return {
        name: "elk", fit: fit !== false, padding: 20, nodeDimensionsIncludeLabels: true, animate: false,
        nodeLayoutOptions: (n) => (n.isParent() ? Object.assign({ "elk.direction": args.direction || "DOWN" }, GROUP)
                                                : undefined),
        elk: { "algorithm": "rectpacking", "elk.hierarchyHandling": "SEPARATE_CHILDREN",
               "elk.aspectRatio": viewerShape(), "elk.spacing.nodeNode": 28 },
      };
    }
    return {
      name: "elk", fit: fit !== false, padding: 20, nodeDimensionsIncludeLabels: true, animate: false,
      elk: {
        "algorithm": args.algorithm || "layered", "elk.direction": args.direction || "RIGHT",
        "elk.hierarchyHandling": "INCLUDE_CHILDREN", "elk.spacing.nodeNode": 28,
        "elk.layered.spacing.nodeNodeBetweenLayers": betweenLayers(), "elk.spacing.edgeNode": 18,
        "elk.layered.nodePlacement.strategy": "BRANDES_KOEPF", "elk.padding": "[top=28,left=12,bottom=12,right=12]",
        "elk.layered.considerModelOrder.strategy": "NODES_AND_EDGES",
        "elk.separateConnectedComponents": true, "elk.aspectRatio": 1.6,
      },
    };
  }

  // A closed group gets one arrow per neighbour and kind of link, not one per hidden box.
  function dedupe() {
    cy.edges().removeClass("dup").removeData("bundle");
    const seen = {};
    cy.edges().forEach((e) => {
      if (e.style("display") === "none") { return; }
      const k = e.source().id() + "|" + e.target().id() + "|" + e.data("relation");
      if (seen[k]) { e.addClass("dup"); seen[k].data("bundle", (seen[k].data("bundle") || 1) + 1); }
      else { seen[k] = e; }
    });
  }

  function openGroups() {
    return cy.nodes().filter((n) => n.isParent() && !n.hasClass("cy-expand-collapse-collapsed-node"))
      .map((n) => n.id());
  }

  function build(data, dark) {
    const box = document.getElementById("cy");
    box.style.height = (args.height || 600) + "px";
    if (cy) { cy.destroy(); }
    cy = cytoscape({ container: box, elements: data.elements, style: style(dark), wheelSensitivity: 0.25,
                     minZoom: 0.05, maxZoom: 3, boxSelectionEnabled: false });
    api = cy.expandCollapse({ layoutBy: layoutOptions(false), fisheye: false, animate: false, undoable: false,
                              cueEnabled: false, groupEdgesOfSameTypeOnCollapse: true, edgeTypeInfo: "relation",
                              allowNestedEdgeCollapse: true });
    const open = new Set(data.open || []);
    const toClose = cy.nodes().filter((n) => n.isParent() && !open.has(n.id()) && !n.data("open"));
    // Close the deepest groups first so their parents can close around them.
    const sorted = toClose.sort((a, b) => b.ancestors().length - a.ancestors().length);
    if (sorted.length) { api.collapse(sorted, { layoutBy: null }); }
    document.getElementById("busy").style.display = "block";
    setTimeout(() => {
      dedupe();
      cy.layout(Object.assign(layoutOptions(true), { stop: () => {
        document.getElementById("busy").style.display = "none";
        // A few boxes would otherwise be blown up to fill the viewer, hiding the lines between them.
        if (cy.zoom() > 1.3) { cy.zoom(1.3); cy.center(); }
      } })).run();
    }, 0);

    cy.on("expandcollapse.afterexpand expandcollapse.aftercollapse", () => setTimeout(dedupe, 0));
    cy.on("tap", "node", (evt) => {
      const n = evt.target;
      clearTimeout(clickTimer);
      clickTimer = setTimeout(() => {
        if (n.isParent() || n.hasClass("cy-expand-collapse-collapsed-node")) {
          if (api.isCollapsible(n)) { api.collapse(n); } else if (api.isExpandable(n)) { api.expand(n); }
          setValue({ action: "groups", open: openGroups() });
        } else if (n.data("key")) {
          setValue({ action: "open", key: n.data("key") });
        }
      }, 260);
    });
    cy.on("dbltap", "node", (evt) => {
      clearTimeout(clickTimer);
      const n = evt.target;
      if (n.data("key") && n.data("functions")) {
        setValue({ action: "functions", key: n.data("key") });
      }
    });
    cy.on("mouseover", "node", (evt) => {
      const n = evt.target;
      if (n.isParent()) { return; }
      const near = n.closedNeighborhood();
      cy.elements().not(near).not(n.ancestors()).addClass("faded");
    });
    cy.on("mouseout", "node", () => cy.elements().removeClass("faded"));
  }

  function find(text) {
    if (!cy || !text) { return; }
    text = text.toLowerCase();
    const all = api.getAllNodes ? cy.nodes() : cy.nodes();
    let hit = cy.nodes().filter((n) => (n.data("label") || "").toLowerCase().includes(text)).first();
    if (!hit.length) {
      // Look inside closed groups too.
      const closed = cy.nodes(".cy-expand-collapse-collapsed-node");
      for (const g of closed) {
        const inside = (g.data("collapsedChildren") || cy.collection())
          .filter((n) => (n.data("label") || "").toLowerCase().includes(text));
        if (inside.length) {
          api.expandRecursively(g, { layoutBy: layoutOptions(false) });
          hit = cy.nodes().filter((n) => (n.data("label") || "").toLowerCase().includes(text)).first();
          setValue({ action: "groups", open: openGroups() });
          break;
        }
      }
    }
    if (hit.length) {
      cy.elements().unselect();
      hit.select();
      cy.animate({ center: { eles: hit }, zoom: Math.max(cy.zoom(), 1) }, { duration: 300 });
    }
  }

  function download(name, url) {
    const a = document.createElement("a");
    a.href = url;
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
  }

  function esc(s) {
    return String(s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  // A plain SVG of what's on screen: boxes, groups, arrows and labels.
  function toSvg() {
    const bb = cy.elements(":visible").boundingBox();
    const pad = 20, w = bb.w + 2 * pad, h = bb.h + 2 * pad;
    const X = (x) => x - bb.x1 + pad, Y = (y) => y - bb.y1 + pad;
    const out = [`<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" ` +
                 `font-family="Segoe UI, sans-serif" font-size="11">`,
                 `<defs><marker id="arrow" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="7" markerHeight="7" ` +
                 `orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#777"/></marker></defs>`,
                 `<rect width="100%" height="100%" fill="white"/>`];
    const nodes = cy.nodes(":visible").sort((a, b) => a.ancestors().length - b.ancestors().length);
    nodes.forEach((n) => {
      const b = n.boundingBox({ includeLabels: false });
      const kind = n.data("kind") || "other";
      const [fill, stroke] = n.isParent() ? ["#f6f7f9", "#c5c8d0"] : (COLORS[kind] || COLORS.other);
      out.push(`<rect x="${X(b.x1)}" y="${Y(b.y1)}" width="${b.w}" height="${b.h}" rx="6" fill="${fill}" ` +
               `stroke="${stroke}"${n.isParent() ? ' stroke-dasharray="4 3"' : ""}/>`);
      const ty = n.isParent() ? Y(b.y1) + 14 : Y((b.y1 + b.y2) / 2) + 4;
      out.push(`<text x="${X((b.x1 + b.x2) / 2)}" y="${ty}" text-anchor="middle"` +
               `${n.isParent() ? ' font-weight="bold"' : ""}>${esc(n.data("label"))}</text>`);
    });
    cy.edges(":visible").forEach((e) => {
      const s = e.sourceEndpoint(), t = e.targetEndpoint();
      const color = EDGE[e.data("relation")] || "#999";
      const dash = ["reads", "suggested", "uses"].includes(e.data("relation")) ? ' stroke-dasharray="5 4"' : "";
      out.push(`<line x1="${X(s.x)}" y1="${Y(s.y)}" x2="${X(t.x)}" y2="${Y(t.y)}" stroke="${color}" ` +
               `stroke-width="1.4"${dash} marker-end="url(#arrow)"/>`);
    });
    out.push("</svg>");
    return out.join("\n");
  }

  document.getElementById("zin").onclick = () => cy && cy.zoom({ level: cy.zoom() * 1.25,
                                                                  renderedPosition: { x: cy.width() / 2, y: cy.height() / 2 } });
  document.getElementById("zout").onclick = () => cy && cy.zoom({ level: cy.zoom() / 1.25,
                                                                   renderedPosition: { x: cy.width() / 2, y: cy.height() / 2 } });
  document.getElementById("fit").onclick = () => cy && cy.fit(undefined, 20);
  document.getElementById("open-all").onclick = () => {
    if (!cy) { return; }
    api.expandAll({ layoutBy: layoutOptions(true) });
    setValue({ action: "groups", open: openGroups() });
  };
  document.getElementById("close-all").onclick = () => {
    if (!cy) { return; }
    api.collapseAll({ layoutBy: layoutOptions(true) });
    setValue({ action: "groups", open: [] });
  };
  document.getElementById("find").addEventListener("keydown", (e) => {
    if (e.key === "Enter") { find(e.target.value.trim()); }
  });
  document.getElementById("png").onclick = () => cy && download((args.name || "graph") + ".png",
    cy.png({ full: true, scale: 2, bg: "#ffffff" }));
  document.getElementById("svg").onclick = () => cy && download((args.name || "graph") + ".svg",
    "data:image/svg+xml;charset=utf-8," + encodeURIComponent(toSvg()));

  window.addEventListener("message", (event) => {
    const msg = event.data;
    if (!msg || msg.type !== "streamlit:render") { return; }
    args = msg.args || {};
    const theme = msg.theme || {};
    const dark = theme.base === "dark";
    if (dark) {
      const r = document.documentElement.style;
      r.setProperty("--bg", theme.backgroundColor || "#0e1117");
      r.setProperty("--fg", theme.textColor || "#fafafa");
      r.setProperty("--panel", theme.secondaryBackgroundColor || "#262730");
      r.setProperty("--line", "#3b3d45");
    }
    const data = JSON.stringify([args.elements, args.direction, args.algorithm, args.packed, dark]);
    if (data !== lastData) {
      lastData = data;
      build({ elements: args.elements || [], open: args.open || [] }, dark);
    }
    send("streamlit:setFrameHeight", { height: (args.height || 600) + 48 });
  });
  send("streamlit:componentReady", { apiVersion: 1 });
})();
