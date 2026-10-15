// Abstract hero graphic: data particles travel from edge devices through gateways and a platform core to outputs.
(() => {
  const root = document.querySelector("[data-network]");
  if (!root) return;

  const NS = "http://www.w3.org/2000/svg";
  const W = 520;
  const H = 440;
  const SPEED = 120; // viewBox units per second
  const COLORS = ["#34558b", "#34558b", "#34558b", "#8fa3c8", "#8fa3c8", "#e0a93b"];

  const edgeNodes = [[70, 48], [50, 112], [78, 176], [46, 240], [80, 304], [54, 368], [84, 410]].map(([x, y], i) => ({ id: `e${i}`, x, y, kind: "edge" }));
  const gateways = [{ id: "g0", x: 200, y: 150, kind: "gw" }, { id: "g1", x: 200, y: 300, kind: "gw" }];
  const core = { id: "core", x: 330, y: 225, kind: "core" };
  const outputs = [82, 176, 274, 368].map((y, i) => ({ id: `o${i}`, x: 466, y, kind: "out" }));
  const nodes = [...edgeNodes, ...gateways, core, ...outputs];
  const byId = Object.fromEntries(nodes.map((n) => [n.id, n]));

  const links = [
    ...edgeNodes.map((e) => [e.id, e.y < 225 ? "g0" : "g1"]),
    ...gateways.map((g) => [g.id, "core"]),
    ...outputs.map((o) => ["core", o.id]),
  ];

  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)");
  const toggle = root.querySelector("[data-network-toggle]");
  const paths = new Map();
  const nodeEls = new Map();
  let layer;
  let particles = [];
  let running = true;
  let visible = false;
  let raf = 0;
  let last = 0;
  let spawnIn = 0;

  const make = (name, attrs, parent) => {
    const e = document.createElementNS(NS, name);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  };

  const curve = (a, b) => {
    const mx = (a.x + b.x) / 2;
    return `M${a.x} ${a.y} C${mx} ${a.y} ${mx} ${b.y} ${b.x} ${b.y}`;
  };

  function render() {
    const svg = make("svg", { viewBox: `0 0 ${W} ${H}`, class: "net-svg", "aria-hidden": "true", focusable: "false" });
    svg.innerHTML =
      '<defs>' +
      '<pattern id="net-dots" width="16" height="16" patternUnits="userSpaceOnUse"><circle cx="2" cy="2" r="1" class="net-dot"/></pattern>' +
      '<radialGradient id="net-fade"><stop offset="0.5" stop-color="#fff"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></radialGradient>' +
      '<mask id="net-mask"><rect width="520" height="440" fill="url(#net-fade)"/></mask>' +
      '<radialGradient id="net-halo"><stop offset="0" stop-color="#34558b" stop-opacity="0.16"/><stop offset="1" stop-color="#34558b" stop-opacity="0"/></radialGradient>' +
      '<filter id="net-glow" x="-200%" y="-200%" width="500%" height="500%"><feGaussianBlur stdDeviation="2.2" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>' +
      "</defs>";
    make("rect", { width: W, height: H, fill: "url(#net-dots)", mask: "url(#net-mask)" }, svg);
    make("circle", { cx: core.x, cy: core.y, r: 92, fill: "url(#net-halo)" }, svg);
    make("circle", { cx: core.x, cy: core.y, r: 44, class: "net-orbit" }, svg);
    make("circle", { cx: core.x, cy: core.y, r: 62, class: "net-orbit net-orbit-slow" }, svg);

    const linkLayer = make("g", { class: "net-links" }, svg);
    for (const [a, b] of links) paths.set(`${a}>${b}`, make("path", { d: curve(byId[a], byId[b]) }, linkLayer));

    const nodeLayer = make("g", { class: "net-nodes" }, svg);
    for (const n of nodes) {
      const g = make("g", { class: `net-node net-${n.kind}`, transform: `translate(${n.x} ${n.y})` }, nodeLayer);
      if (n.kind === "edge") make("circle", { r: 5 }, g);
      if (n.kind === "gw") {
        make("circle", { r: 9 }, g);
        make("circle", { r: 3, class: "net-inner" }, g);
      }
      if (n.kind === "core") {
        make("circle", { r: 26, class: "net-pulse" }, g);
        make("circle", { r: 26 }, g);
        make("circle", { r: 9, class: "net-inner" }, g);
      }
      if (n.kind === "out") make("rect", { x: -10, y: -10, width: 20, height: 20, rx: 5 }, g);
      nodeEls.set(n.id, g);
    }

    layer = make("g", { class: "net-particles" }, svg);
    root.prepend(svg);
  }

  const pick = (list) => list[Math.floor(Math.random() * list.length)];

  function spawn() {
    const from = pick(edgeNodes);
    const route = [from.id, from.y < 225 ? "g0" : "g1", "core", pick(outputs).id];
    const color = pick(COLORS);
    const g = make("g", { class: "net-particle" }, layer);
    const trail2 = make("circle", { r: 1.8, fill: color, opacity: 0.2 }, g);
    const trail1 = make("circle", { r: 2.4, fill: color, opacity: 0.45 }, g);
    const head = make("circle", { r: 3.2, fill: color, filter: "url(#net-glow)" }, g);
    particles.push({ route, hop: 0, dist: 0, wait: 0, g, dots: [head, trail1, trail2] });
  }

  function hit(id) {
    const g = nodeEls.get(id);
    if (!g) return;
    g.classList.remove("hit");
    g.getBoundingClientRect();
    g.classList.add("hit");
  }

  function step(dt) {
    spawnIn -= dt;
    if (spawnIn <= 0 && particles.length < 12) {
      spawn();
      spawnIn = 0.25 + Math.random() * 0.45;
    }
    for (const p of particles) {
      if (p.wait > 0) {
        p.wait -= dt;
        continue;
      }
      const path = paths.get(`${p.route[p.hop]}>${p.route[p.hop + 1]}`);
      const len = path.getTotalLength();
      p.dist += SPEED * dt;
      if (p.dist >= len) {
        p.hop += 1;
        p.dist = 0;
        const at = p.route[p.hop];
        hit(at);
        if (p.hop >= p.route.length - 1) {
          p.done = true;
          p.g.remove();
        } else {
          p.wait = at === "core" ? 0.3 : 0.1;
          p.g.style.opacity = at === "core" ? "0" : "";
        }
        continue;
      }
      p.g.style.opacity = "";
      [0, 6, 12].forEach((back, i) => {
        const pt = path.getPointAtLength(Math.max(0, p.dist - back));
        p.dots[i].setAttribute("cx", pt.x.toFixed(1));
        p.dots[i].setAttribute("cy", pt.y.toFixed(1));
      });
    }
    particles = particles.filter((p) => !p.done);
  }

  function frame(ts) {
    raf = 0;
    const dt = last ? Math.min(0.05, (ts - last) / 1000) : 0;
    last = ts;
    step(dt);
    kick();
  }

  function kick() {
    const go = running && visible && !document.hidden && !reduce.matches;
    if (go && !raf) raf = requestAnimationFrame(frame);
    if (!go) last = 0;
  }

  render();
  new IntersectionObserver(([entry]) => {
    visible = entry.isIntersecting;
    kick();
  }).observe(root);
  document.addEventListener("visibilitychange", kick);

  if (toggle) {
    if (reduce.matches) toggle.hidden = true;
    toggle.addEventListener("click", () => {
      running = !running;
      root.classList.toggle("is-paused", !running);
      toggle.setAttribute("aria-pressed", String(!running));
      toggle.setAttribute("aria-label", running ? toggle.dataset.pause : toggle.dataset.play);
      kick();
    });
  }
})();
