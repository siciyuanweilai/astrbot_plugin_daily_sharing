const cursorTrailIntervalMs = 72;
const cursorTrailMinDistance = 18;
const cursorTrailMaxItems = 34;
const sakuraDesktopPetals = 34;
const sakuraMobilePetals = 20;

const sakuraDepths = [
  {
    className: "is-far",
    depth: 1,
    sizeScale: 0.76,
    opacityRange: [0.28, 0.42],
    durationRange: [18, 27],
    swayRange: [7, 16],
    windRange: [10, 24],
    spinRange: [120, 340],
  },
  {
    className: "is-mid",
    depth: 2,
    sizeScale: 1,
    opacityRange: [0.44, 0.7],
    durationRange: [13, 21],
    swayRange: [12, 30],
    windRange: [18, 42],
    spinRange: [220, 560],
  },
  {
    className: "is-near",
    depth: 3,
    sizeScale: 1.28,
    opacityRange: [0.58, 0.88],
    durationRange: [10, 16],
    swayRange: [20, 42],
    windRange: [30, 58],
    spinRange: [360, 760],
  },
];

function randomBetween(min, max) {
  return min + Math.random() * (max - min);
}

export function isMotionReduced() {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

export function createDashboardEffects({ sakuraLayer, cursorTrailLayer } = {}) {
  const cursorState = {
    bound: false,
    lastAt: 0,
    lastX: 0,
    lastY: 0,
  };

  function initSakuraFall() {
    if (!sakuraLayer || isMotionReduced()) return;

    const isMobile = window.matchMedia("(max-width: 720px)").matches;
    const petalCount = isMobile ? sakuraMobilePetals : sakuraDesktopPetals;
    sakuraLayer.textContent = "";

    for (let index = 0; index < petalCount; index += 1) {
      const petal = document.createElement("span");
      const shape = document.createElement("span");
      const depth = sakuraDepths[index % sakuraDepths.length];
      const windDirection = Math.random() > 0.32 ? 1 : -1;
      const windStrength = randomBetween(depth.windRange[0], depth.windRange[1]);
      const size = (8 + Math.random() * 10) * depth.sizeScale;
      const left = Math.random() * 100;
      const windA = windDirection * windStrength * randomBetween(0.2, 0.46);
      const windB = windDirection * windStrength * randomBetween(0.68, 1);
      const windC = windDirection * windStrength * randomBetween(0.42, 0.82);
      const drift = windDirection * windStrength + randomBetween(-10, 10);
      const sway = randomBetween(depth.swayRange[0], depth.swayRange[1]);
      const spin = randomBetween(depth.spinRange[0], depth.spinRange[1]);

      petal.className = `sakura-petal ${depth.className}`;
      shape.className = "sakura-petal-shape";
      petal.style.setProperty("--sakura-left", `${left.toFixed(2)}vw`);
      petal.style.setProperty("--sakura-size", `${size.toFixed(1)}px`);
      petal.style.setProperty("--sakura-depth", depth.depth);
      petal.style.setProperty("--sakura-opacity", randomBetween(depth.opacityRange[0], depth.opacityRange[1]).toFixed(2));
      petal.style.setProperty("--sakura-duration", `${randomBetween(depth.durationRange[0], depth.durationRange[1]).toFixed(2)}s`);
      petal.style.setProperty("--sakura-sway-duration", `${randomBetween(2.6, 5.8).toFixed(2)}s`);
      petal.style.setProperty("--sakura-delay", `${(-Math.random() * 18).toFixed(2)}s`);
      petal.style.setProperty("--sakura-wind-a", `${windA.toFixed(1)}vw`);
      petal.style.setProperty("--sakura-wind-b", `${windB.toFixed(1)}vw`);
      petal.style.setProperty("--sakura-wind-c", `${windC.toFixed(1)}vw`);
      petal.style.setProperty("--sakura-drift", `${drift.toFixed(1)}vw`);
      petal.style.setProperty("--sakura-sway", `${sway.toFixed(1)}px`);
      petal.style.setProperty("--sakura-rotate", `${Math.floor(Math.random() * 360)}deg`);
      petal.style.setProperty("--sakura-spin-a", `${(spin * 0.24).toFixed(0)}deg`);
      petal.style.setProperty("--sakura-spin-b", `${(spin * 0.56).toFixed(0)}deg`);
      petal.style.setProperty("--sakura-spin-c", `${(spin * 0.78).toFixed(0)}deg`);
      petal.style.setProperty("--sakura-spin", `${spin.toFixed(0)}deg`);

      petal.appendChild(shape);
      sakuraLayer.appendChild(petal);
    }
  }

  function clearSakuraFall() {
    if (sakuraLayer) sakuraLayer.textContent = "";
  }

  function hasSakuraFall() {
    return Boolean(sakuraLayer?.children.length);
  }

  function isDreamCursorAvailable() {
    return Boolean(cursorTrailLayer)
      && window.matchMedia("(pointer: fine)").matches
      && !isMotionReduced();
  }

  function clearDreamCursorTrail() {
    if (cursorTrailLayer) cursorTrailLayer.textContent = "";
  }

  function createDreamCursorItem(x, y) {
    if (!isDreamCursorAvailable()) {
      clearDreamCursorTrail();
      return;
    }

    while (cursorTrailLayer.children.length >= cursorTrailMaxItems) {
      cursorTrailLayer.firstElementChild?.remove();
    }

    const item = document.createElement("span");
    const sparkle = Math.random() > 0.58;
    const size = sparkle ? randomBetween(9, 17) : randomBetween(10, 20);
    item.className = `cursor-dream${sparkle ? " sparkle" : ""}`;
    item.style.setProperty("--cursor-x", `${x.toFixed(1)}px`);
    item.style.setProperty("--cursor-y", `${y.toFixed(1)}px`);
    item.style.setProperty("--cursor-size", `${size.toFixed(1)}px`);
    item.style.setProperty("--cursor-opacity", sparkle ? randomBetween(0.5, 0.82).toFixed(2) : randomBetween(0.42, 0.72).toFixed(2));
    item.style.setProperty("--cursor-duration", `${randomBetween(680, 1050).toFixed(0)}ms`);
    item.style.setProperty("--cursor-drift-x", `${randomBetween(-28, 24).toFixed(1)}px`);
    item.style.setProperty("--cursor-drift-y", `${randomBetween(-46, -22).toFixed(1)}px`);
    item.style.setProperty("--cursor-rotate", `${randomBetween(-70, 80).toFixed(0)}deg`);
    item.style.setProperty("--cursor-spin", `${randomBetween(80, 230).toFixed(0)}deg`);
    item.style.setProperty("--cursor-scale", randomBetween(0.55, 1.12).toFixed(2));
    item.addEventListener("animationend", () => item.remove(), { once: true });
    cursorTrailLayer.appendChild(item);
  }

  function handleDreamCursorMove(event) {
    if (event.pointerType && event.pointerType !== "mouse") return;
    if (!isDreamCursorAvailable()) return;

    const now = window.performance.now();
    const distance = Math.hypot(event.clientX - cursorState.lastX, event.clientY - cursorState.lastY);
    if (now - cursorState.lastAt < cursorTrailIntervalMs && distance < cursorTrailMinDistance) return;

    cursorState.lastAt = now;
    cursorState.lastX = event.clientX;
    cursorState.lastY = event.clientY;
    createDreamCursorItem(event.clientX, event.clientY);
  }

  function initDreamCursor() {
    if (cursorState.bound) return;
    cursorState.bound = true;
    window.addEventListener("pointermove", handleDreamCursorMove, { passive: true });
    window.addEventListener("pagehide", clearDreamCursorTrail);

    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
    const finePointer = window.matchMedia("(pointer: fine)");
    const clearIfDisabled = () => {
      if (!isDreamCursorAvailable()) clearDreamCursorTrail();
    };
    reducedMotion.addEventListener?.("change", clearIfDisabled);
    finePointer.addEventListener?.("change", clearIfDisabled);
  }

  return {
    clearSakuraFall,
    hasSakuraFall,
    initDreamCursor,
    initSakuraFall,
    isMotionReduced,
  };
}
