(() => {
  "use strict";

  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const finePointer = window.matchMedia("(pointer: fine)").matches;

  /* Reveal content only when JavaScript is available. If IntersectionObserver is
     missing, everything is shown immediately instead of remaining hidden. */
  const revealItems = [...document.querySelectorAll(".reveal")];
  if (reduceMotion || !("IntersectionObserver" in window)) {
    revealItems.forEach((item) => item.classList.add("is-visible"));
  } else {
    const revealObserver = new IntersectionObserver(
      (entries, observer) => {
        entries.forEach((entry) => {
          if (!entry.isIntersecting) return;
          entry.target.classList.add("is-visible");
          observer.unobserve(entry.target);
        });
      },
      { threshold: 0.11, rootMargin: "0px 0px -7%" },
    );
    revealItems.forEach((item) => revealObserver.observe(item));
  }

  /* Header state is the only scroll listener on the page. It is rAF-gated and
     does not read layout, so the main visual work stays pointer/observer driven. */
  const header = document.querySelector("[data-header]");
  let headerTicking = false;
  const updateHeader = () => {
    headerTicking = false;
    header?.classList.toggle("is-scrolled", window.scrollY > 22);
  };
  window.addEventListener(
    "scroll",
    () => {
      if (headerTicking) return;
      headerTicking = true;
      requestAnimationFrame(updateHeader);
    },
    { passive: true },
  );
  updateHeader();

  /* Signature interaction: one lighthouse beam controls both the visible light
     cone and the circular Chinese layer on the identically laid-out paper. */
  const demo = document.querySelector("[data-translation-demo]");
  const stage = document.querySelector("[data-paper-stage]");

  if (reduceMotion && stage) {
    stage.removeAttribute("tabindex");
    stage.setAttribute("aria-label", "英文与中文的静态对照；中文显示在论文右半侧");
  }

  if (demo && stage && !reduceMotion) {
    const translatedPaper = stage.querySelector(".paper-sheet--zh");
    let stageRect = stage.getBoundingClientRect();
    let paperOffset = {
      left: translatedPaper?.offsetLeft ?? 0,
      top: translatedPaper?.offsetTop ?? 0,
    };
    let userUntil = 0;
    let demoVisible = true;
    let rafId = 0;
    let beamPosition = { x: stageRect.width * 0.68, y: stageRect.height * 0.42 };

    const clamp = (value, min, max) => Math.min(max, Math.max(min, value));

    const refreshStageRect = () => {
      stageRect = stage.getBoundingClientRect();
      paperOffset = {
        left: translatedPaper?.offsetLeft ?? 0,
        top: translatedPaper?.offsetTop ?? 0,
      };
    };

    const placeBeam = (x, y) => {
      const width = stageRect.width;
      const height = stageRect.height;
      const safeX = clamp(x, 28, Math.max(29, width - 28));
      const safeY = clamp(y, 30, Math.max(31, height - 52));
      beamPosition = { x: safeX, y: safeY };
      const originX = width / 2;
      const originY = height - 13;
      const dx = safeX - originX;
      const dy = safeY - originY;
      const angle = Math.atan2(dy, dx) * (180 / Math.PI);
      const distance = Math.hypot(dx, dy);

      stage.style.setProperty("--beam-x", `${safeX.toFixed(1)}px`);
      stage.style.setProperty("--beam-y", `${safeY.toFixed(1)}px`);
      stage.style.setProperty("--paper-beam-x", `${(safeX - paperOffset.left).toFixed(1)}px`);
      stage.style.setProperty("--paper-beam-y", `${(safeY - paperOffset.top).toFixed(1)}px`);
      stage.style.setProperty("--beam-angle", `${angle.toFixed(2)}deg`);
      stage.style.setProperty("--beam-distance", `${distance.toFixed(1)}px`);
    };

    const autoBeam = (time) => {
      if (!demoVisible) {
        rafId = 0;
        return;
      }
      if (time > userUntil) {
        const width = stageRect.width;
        const height = stageRect.height;
        const t = time / 1700;
        const x = width * (0.5 + Math.sin(t * 0.72) * 0.27);
        const y = height * (0.4 + Math.sin(t * 0.93 + 1.2) * 0.16);
        placeBeam(x, y);
      }
      rafId = requestAnimationFrame(autoBeam);
    };

    const engageAtPointer = (event) => {
      refreshStageRect();
      userUntil = performance.now() + 2300;
      demo.classList.add("is-engaged");
      placeBeam(event.clientX - stageRect.left, event.clientY - stageRect.top);
    };

    stage.addEventListener("pointermove", engageAtPointer, { passive: true });
    stage.addEventListener(
      "pointerdown",
      (event) => {
        stage.focus({ preventScroll: true });
        engageAtPointer(event);
      },
      { passive: true },
    );
    stage.addEventListener("pointerleave", () => demo.classList.remove("is-engaged"));
    stage.addEventListener("keydown", (event) => {
      const movement = {
        ArrowLeft: [-18, 0],
        ArrowRight: [18, 0],
        ArrowUp: [0, -18],
        ArrowDown: [0, 18],
      }[event.key];
      if (!movement) return;
      event.preventDefault();
      userUntil = performance.now() + 2800;
      demo.classList.add("is-engaged");
      placeBeam(beamPosition.x + movement[0], beamPosition.y + movement[1]);
    });

    window.addEventListener("resize", refreshStageRect, { passive: true });
    if ("ResizeObserver" in window) {
      new ResizeObserver(refreshStageRect).observe(stage);
    }

    if ("IntersectionObserver" in window) {
      const beamObserver = new IntersectionObserver(
        ([entry]) => {
          demoVisible = Boolean(entry?.isIntersecting);
          if (demoVisible) {
            refreshStageRect();
            if (!rafId) rafId = requestAnimationFrame(autoBeam);
          } else if (rafId) {
            cancelAnimationFrame(rafId);
            rafId = 0;
          }
        },
        { threshold: 0.01 },
      );
      beamObserver.observe(demo);
    }

    placeBeam(stageRect.width * 0.68, stageRect.height * 0.42);
    rafId = requestAnimationFrame(autoBeam);

    window.addEventListener(
      "pagehide",
      () => {
        if (rafId) cancelAnimationFrame(rafId);
      },
      { once: true },
    );
  }

  /* The workflow is a tabbed product scene, not four separate feature cards.
     Mouse, touch and keyboard all drive the same explicit selected state. */
  const routeTabs = [...document.querySelectorAll("[data-route]")];
  const routeScenes = [...document.querySelectorAll("[data-scene]")];
  const routeProgress = [...document.querySelectorAll(".route-stage__progress i")];

  const selectRoute = (name, focus = false) => {
    const index = routeTabs.findIndex((tab) => tab.dataset.route === name);
    if (index < 0) return;

    routeTabs.forEach((tab, tabIndex) => {
      const active = tabIndex === index;
      tab.classList.toggle("is-active", active);
      tab.setAttribute("aria-selected", String(active));
      tab.tabIndex = active ? 0 : -1;
      if (active && focus) tab.focus();
    });

    routeScenes.forEach((scene) => {
      const active = scene.dataset.scene === name;
      scene.classList.toggle("is-active", active);
      scene.setAttribute("aria-hidden", String(!active));
    });

    routeProgress.forEach((bar, barIndex) => {
      bar.classList.toggle("is-on", barIndex <= index);
    });
  };

  routeTabs.forEach((tab, index) => {
    tab.addEventListener("click", () => selectRoute(tab.dataset.route));
    tab.addEventListener("keydown", (event) => {
      if (!["ArrowDown", "ArrowRight", "ArrowUp", "ArrowLeft", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      let nextIndex = index;
      if (event.key === "ArrowDown" || event.key === "ArrowRight") nextIndex = (index + 1) % routeTabs.length;
      if (event.key === "ArrowUp" || event.key === "ArrowLeft") nextIndex = (index - 1 + routeTabs.length) % routeTabs.length;
      if (event.key === "Home") nextIndex = 0;
      if (event.key === "End") nextIndex = routeTabs.length - 1;
      selectRoute(routeTabs[nextIndex].dataset.route, true);
    });
  });
  if (routeTabs.length) selectRoute(routeTabs[0].dataset.route);

  /* The mode pills in the product mock are deliberately operable. They do not
     fake a backend request; they simply show the selected reading mode. */
  document.querySelectorAll(".reader-modebar button").forEach((button) => {
    button.addEventListener("click", () => {
      button.parentElement
        ?.querySelectorAll("button")
        .forEach((peer) => {
          const active = peer === button;
          peer.classList.toggle("is-active", active);
          peer.setAttribute("aria-pressed", String(active));
        });
    });
  });

  /* One restrained piece of depth on the owned illustration. It never runs on
     touch devices, reduced-motion setups, or while the pointer is elsewhere. */
  const posterFrame = document.querySelector("[data-poster-frame]");
  const posterImage = posterFrame?.querySelector("img");
  if (posterFrame && posterImage && finePointer && !reduceMotion) {
    posterFrame.addEventListener(
      "pointermove",
      (event) => {
        const rect = posterFrame.getBoundingClientRect();
        const x = (event.clientX - rect.left) / rect.width - 0.5;
        const y = (event.clientY - rect.top) / rect.height - 0.5;
        posterImage.style.transform = `translate3d(${(x * -9).toFixed(1)}px, ${(y * -7).toFixed(1)}px, 0) scale(1.018)`;
      },
      { passive: true },
    );
    posterFrame.addEventListener("pointerleave", () => {
      posterImage.style.transform = "";
    });
  }
})();
