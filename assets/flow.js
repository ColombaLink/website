// Architecture diagram: pigeons carry data packets from devices to the cloud and on to people.
(() => {
  const root = document.querySelector("[data-flow]");
  if (!root) return;

  const SVGNS = "http://www.w3.org/2000/svg";
  const de = (document.documentElement.lang || "").startsWith("de");
  const L = de
    ? {
        temp: "Temperatur",
        air: "CO₂ & Luft",
        leak: "Leck",
        energy: "Energie",
        gw: "LoRaWAN-\nGateway",
        ingest: "Daten-\naufnahme",
        store: "Zeitreihen-\nspeicher",
        analytics: "Analyse\n& ML",
        dash: "Dashboards",
        alerts: "Alarme",
        api: "APIs &\nSchnittstellen",
        cloud: "IHRE INFRASTRUKTUR · KUBERNETES",
        field: "IM FELD",
        people: "FÜR MENSCHEN & SYSTEME",
        title: "Architektur des Datenflusses",
        desc: "Sensoren für Temperatur, CO₂, Lecks und Energie senden Daten über ein privates LoRaWAN-Gateway an Kubernetes auf Ihrer eigenen Infrastruktur. Dort werden sie aufgenommen, als Zeitreihen gespeichert und analysiert; die Ergebnisse gehen an Dashboards, Alarme und APIs.",
      }
    : {
        temp: "Temperature",
        air: "CO₂ & air",
        leak: "Leak",
        energy: "Energy",
        gw: "LoRaWAN\ngateway",
        ingest: "Ingestion",
        store: "Time-series\nstorage",
        analytics: "Analytics\n& ML",
        dash: "Dashboards",
        alerts: "Alarms",
        api: "APIs &\nintegrations",
        cloud: "YOUR INFRASTRUCTURE · KUBERNETES",
        field: "IN THE FIELD",
        people: "FOR PEOPLE & SYSTEMS",
        title: "Data flow architecture",
        desc: "Sensors for temperature, CO₂, leaks and energy send data through a private LoRaWAN gateway to Kubernetes on your own infrastructure, where it is ingested, stored as time series and analysed; results go to dashboards, alarms and APIs.",
      };

  const ICONS = {
    temp: '<path d="M14 4v10.54a4 4 0 1 1-4 0V4a2 2 0 0 1 4 0Z"/>',
    air: '<path d="M17.7 7.7a2.5 2.5 0 1 1 1.8 4.3H2"/><path d="M9.6 4.6A2 2 0 1 1 11 8H2"/><path d="M12.6 19.4A2 2 0 1 0 14 16H2"/>',
    leak: '<path d="M12 22a7 7 0 0 0 7-7c0-2-1-3.9-3-5.5s-3.5-4-4-6.5c-.5 2.5-2 4.9-4 6.5C6 11.1 5 13 5 15a7 7 0 0 0 7 7z"/>',
    energy: '<path d="M13 2 4 14h7l-1 8 9-12h-7l1-8z"/>',
    gw: '<path d="M4.9 16.1C1 12.2 1 5.8 4.9 1.9"/><path d="M7.8 4.7a6.1 6.1 0 0 0-.8 7.5"/><circle cx="12" cy="9" r="2"/><path d="M16.2 4.8c2 2 2.3 5.1.8 7.5"/><path d="M19.1 1.9a10 10 0 0 1 0 14.1"/><path d="m8 22 4-11 4 11"/><path d="M9.5 18h5"/>',
    ingest: '<rect x="2" y="2" width="20" height="8" rx="2"/><rect x="2" y="14" width="20" height="8" rx="2"/><path d="M6 6h.01M6 18h.01"/>',
    store: '<ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5v14a9 3 0 0 0 18 0V5"/><path d="M3 12a9 3 0 0 0 18 0"/>',
    analytics: '<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>',
    dash: '<rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8M12 17v4"/><path d="m6 13 3-3 3 2 5-5"/>',
    alerts: '<path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/>',
    api: '<path d="m18 16 4-4-4-4"/><path d="m6 8-4 4 4 4"/><path d="m14.5 4-5 16"/>',
  };

  // Packet colour per origin device.
  const NODES = [
    { id: "temp", wide: [90, 80], tall: [60, 80], packet: "#f0c36d" },
    { id: "air", wide: [90, 193], tall: [160, 80], packet: "#7ad0c4" },
    { id: "leak", wide: [90, 306], tall: [260, 80], packet: "#7fb2ff" },
    { id: "energy", wide: [90, 420], tall: [360, 80], packet: "#ffad6b" },
    { id: "gw", wide: [310, 250], tall: [210, 230] },
    { id: "ingest", wide: [540, 150], tall: [115, 420] },
    { id: "store", wide: [540, 350], tall: [305, 420] },
    { id: "analytics", wide: [770, 250], tall: [210, 555] },
    { id: "dash", wide: [1020, 100], tall: [70, 760] },
    { id: "alerts", wide: [1020, 250], tall: [210, 760] },
    { id: "api", wide: [1020, 400], tall: [350, 760] },
  ];
  const EDGES = [
    ["temp", "gw"], ["air", "gw"], ["leak", "gw"], ["energy", "gw"],
    ["gw", "ingest"], ["ingest", "store"], ["ingest", "analytics"], ["store", "analytics"],
    ["analytics", "dash"], ["analytics", "alerts"], ["analytics", "api"],
  ];
  const DEVICES = ["temp", "air", "leak", "energy"];
  const MIDDLES = [["gw", "ingest", "store", "analytics"], ["gw", "ingest", "analytics"]];
  const OUTPUTS = ["dash", "alerts", "api"];

  const LAYOUTS = {
    wide: { w: 1120, h: 500, dir: "h", cloud: [450, 60, 410, 380], cloudLabel: [468, 86, "start"], field: [90, 34], people: [1020, 50] },
    tall: { w: 420, h: 860, dir: "v", cloud: [30, 330, 360, 330], cloudLabel: [48, 646, "start"], field: [210, 30], people: [210, 700] },
  };

  // The bird is the logo pigeon (vector from the brand kit), with a raised wing behind it that flaps and a packet in its feet.
  const PIGEON =
    '<path class="pg-wing" d="M6 -3 C2 -15 -6 -23 -17 -25 C-11 -17 -8 -9 -5 2 Z"/>' +
    '<g class="pg-packet"><rect x="-2" y="17" width="13" height="9.5" rx="1.6"/><path d="M-2 18 L4.5 22.5 L11 18"/></g>' +
    '<g class="pg-logo" transform="matrix(0.2081 0 0 0.2081 20.55 5.81)">' +
    '<path class="pg-body" d="M-87.9-106.5c2.3,17.9,11.1,49.8-6.9,66.3c-7,6-20.5,30-22.6,35.9c-10.2,28.6-47.1,31.4-82.2,28.8c25,19.4,77.2,26.3,121.9,21.8c3.9,14.1,7.4,18.3,4.6,21.6c-2.9,3.4-12.8,2.1-12.2,5.1h67c-4.4-13.1-26.8,12.4-33-30.3c0,0,14.1-3.6,18.6-5c24.9-69.2,4.3-109.4-12.2-140.7l17.3,2.2c-1.9-3.4-10.5-10.4-16.6-11.5c-5.6-17.9-17.1-22.2-27.5-21.2C-81.5-132.7-90.4-121.5-87.9-106.5z M-59.2,44.2c5.7,20.3,8.8,24.1-0.4,24.3c-5.8-2.5-8.1-9.3-9.5-23.1C-65.7,45-62.3,44.6-59.2,44.2z"/>' +
    '<path class="pg-belly" d="M-53.3-54.3c-58.4-16-123,137.9,20.7,91.8c-147.2,30.3-48.1-126-21.3-64c-35.8-30.2-96.6,77.4,17.1,58.7C-33.7,2.1-29.2-47.7-53.3-54.3z"/>' +
    '<ellipse class="pg-beak" transform="matrix(0.2014 -0.9795 0.9795 0.2014 73.3304 -133.389)" cx="-45.1" cy="-111.7" rx="3.4" ry="5.8"/>' +
    '<g transform="matrix(1.036187 0 0 1.036187 -540.00917 149.03059)"><circle class="pg-iris" cx="462.6" cy="-258.9" r="4.3"/><circle class="pg-pupil" cx="462.6" cy="-258.9" r="2"/></g>' +
    '</g>';

  const SPEED = 190; // viewBox units per second
  const PAUSE = 0.28; // seconds a pigeon rests on each node

  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)");
  const toggle = document.querySelector("[data-flow-toggle]");
  const edgeEls = new Map();
  const nodeEls = new Map();
  let layoutName = null;
  let birdLayer = null;
  let pigeons = [];
  let running = true;
  let visible = false;
  let raf = 0;
  let last = 0;
  let spawnIn = 0;

  const make = (name, attrs, parent) => {
    const e = document.createElementNS(SVGNS, name);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  };

  const text = (parent, x, y, str, anchor, cls) => {
    const t = make("text", { x, y, "text-anchor": anchor, class: cls || "" }, parent);
    str.split("\n").forEach((line, i) => {
      const s = make("tspan", { x, dy: i ? "1.2em" : 0 }, t);
      s.textContent = line;
    });
    return t;
  };

  const curve = ([x1, y1], [x2, y2], dir) => {
    if (dir === "h") {
      const mx = (x1 + x2) / 2;
      return `M${x1} ${y1} C${mx} ${y1} ${mx} ${y2} ${x2} ${y2}`;
    }
    const my = (y1 + y2) / 2;
    return `M${x1} ${y1} C${x1} ${my} ${x2} ${my} ${x2} ${y2}`;
  };

  function render() {
    const name = root.clientWidth >= 760 ? "wide" : "tall";
    if (name === layoutName) return;
    layoutName = name;
    const lay = LAYOUTS[name];
    const pos = Object.fromEntries(NODES.map((n) => [n.id, n[name]]));

    pigeons = [];
    edgeEls.clear();
    nodeEls.clear();
    root.querySelector("svg")?.remove();

    const svg = make("svg", {
      viewBox: `0 0 ${lay.w} ${lay.h}`,
      role: "img",
      "aria-labelledby": "flow-title flow-desc",
      class: `flow-svg flow-${name}`,
    });
    make("title", { id: "flow-title" }, svg).textContent = L.title;
    make("desc", { id: "flow-desc" }, svg).textContent = L.desc;

    const [cx, cy, cw, ch] = lay.cloud;
    const cloud = make("g", { class: "flow-cloud" }, svg);
    make("rect", { x: cx, y: cy, width: cw, height: ch, rx: 18 }, cloud);
    text(cloud, lay.cloudLabel[0], lay.cloudLabel[1], L.cloud, lay.cloudLabel[2], "flow-caption");
    text(svg, lay.field[0], lay.field[1], L.field, "middle", "flow-caption");
    text(svg, lay.people[0], lay.people[1], L.people, "middle", "flow-caption");

    const edges = make("g", { class: "flow-edges" }, svg);
    for (const [a, b] of EDGES) {
      edgeEls.set(`${a}>${b}`, make("path", { d: curve(pos[a], pos[b], lay.dir) }, edges));
    }

    const nodes = make("g", { class: "flow-nodes" }, svg);
    for (const n of NODES) {
      const [x, y] = pos[n.id];
      const g = make("g", { class: "flow-node", transform: `translate(${x} ${y})` }, nodes);
      make("circle", { class: "ring", r: 30 }, g);
      make("circle", { class: "disc", r: 30 }, g);
      make("g", { class: "icon", transform: "translate(-12 -12)" }, g).innerHTML = ICONS[n.id];
      text(g, 0, 52, L[n.id], "middle");
      nodeEls.set(n.id, g);
    }

    birdLayer = make("g", { class: "flow-birds" }, svg);
    root.prepend(svg);

    if (reduce.matches) perchStatic();
    else spawnIn = 0;
  }

  function addBird(color) {
    const g = make("g", { class: "pigeon" }, birdLayer);
    g.innerHTML = PIGEON;
    g.querySelector(".pg-packet rect").setAttribute("fill", color);
    const delay = `${-Math.random() * 0.3}s`;
    g.querySelectorAll(".pg-wing").forEach((w) => (w.style.animationDelay = delay));
    return g;
  }

  const pick = (list) => list[Math.floor(Math.random() * list.length)];

  function spawn() {
    const from = pick(DEVICES);
    const to = pick(OUTPUTS);
    const route = [from, ...pick(MIDDLES), to];
    const color = NODES.find((n) => n.id === from).packet;
    pigeons.push({ route, hop: 0, dist: 0, wait: 0, el: addBird(color), alarm: to === "alerts" && Math.random() < 0.4 });
  }

  // Position along an edge, lifted into a gentle arc so the birds fly rather than slide.
  function pointAt(path, len, d) {
    const p = path.getPointAtLength(d);
    return [p.x, p.y - Math.sin((Math.PI * d) / len) * Math.min(26, len * 0.12)];
  }

  function place(bird, x, y, angle, flip) {
    bird.el.setAttribute("transform", `translate(${x.toFixed(1)} ${y.toFixed(1)}) scale(${flip} 1) rotate(${(angle + 10).toFixed(1)})`);
  }

  function pulse(id, alarm) {
    const g = nodeEls.get(id);
    if (!g) return;
    g.classList.remove("hit", "alarm");
    g.getBoundingClientRect();
    g.classList.add(alarm ? "alarm" : "hit");
  }

  function step(dt) {
    spawnIn -= dt;
    if (spawnIn <= 0 && pigeons.length < (layoutName === "wide" ? 6 : 4)) {
      spawn();
      spawnIn = 0.8 + Math.random() * 0.9;
    }
    for (const bird of pigeons) {
      if (bird.wait > 0) {
        bird.wait -= dt;
        continue;
      }
      const path = edgeEls.get(`${bird.route[bird.hop]}>${bird.route[bird.hop + 1]}`);
      const len = path.getTotalLength();
      bird.dist += SPEED * dt;
      if (bird.dist >= len) {
        bird.hop += 1;
        bird.dist = 0;
        const at = bird.route[bird.hop];
        pulse(at, bird.alarm && at === "alerts");
        if (bird.hop >= bird.route.length - 1) {
          bird.done = true;
          bird.el.remove();
        } else {
          bird.wait = PAUSE;
        }
        continue;
      }
      const [x, y] = pointAt(path, len, bird.dist);
      const [x2, y2] = pointAt(path, len, Math.min(len, bird.dist + 3));
      const dx = x2 - x;
      const flip = dx < -0.01 ? -1 : 1;
      const angle = Math.max(-22, Math.min(22, (Math.atan2(y2 - y, Math.abs(dx)) * 180) / Math.PI));
      place(bird, x, y, angle, flip);
    }
    pigeons = pigeons.filter((b) => !b.done);
  }

  function perchStatic() {
    const perches = [["temp", "gw", 0.55], ["gw", "ingest", 0.5], ["store", "analytics", 0.5], ["analytics", "dash", 0.6]];
    perches.forEach(([a, b, t], i) => {
      const path = edgeEls.get(`${a}>${b}`);
      const len = path.getTotalLength();
      const [x, y] = pointAt(path, len, len * t);
      place({ el: addBird(NODES[i].packet) }, x, y, 0, 1);
    });
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
  new ResizeObserver(render).observe(root);
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
      toggle.textContent = running ? toggle.dataset.pause : toggle.dataset.play;
      kick();
    });
  }
})();
