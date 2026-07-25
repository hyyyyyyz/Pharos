(() => {
  "use strict";

  const reduceMotionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
  const reduceMotion = reduceMotionQuery.matches;
  const finePointer = window.matchMedia("(pointer: fine)").matches;

  /* Reveal content only when JavaScript is available. If IntersectionObserver is
     missing, everything is shown immediately instead of remaining hidden. */
  const revealItems = [...document.querySelectorAll(".reveal")];
  document.documentElement.classList.add("js-enhanced");
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

  /* Header state is rAF-gated. The dark-surface boundary is cached so scrolling
     can switch treatments without forcing layout on every frame. */
  const header = document.querySelector("[data-header]");
  const darkHeaderSurface = document.querySelector("[data-beacon-hero]");
  let darkHeaderBoundary = 0;
  let headerTicking = false;
  const refreshHeaderBoundary = () => {
    darkHeaderBoundary = darkHeaderSurface
      ? darkHeaderSurface.offsetTop + darkHeaderSurface.offsetHeight - 76
      : 0;
  };
  const updateHeader = () => {
    headerTicking = false;
    const scrollPosition = window.scrollY;
    header?.classList.toggle("is-scrolled", scrollPosition > 22);
    header?.classList.toggle("is-over-dark", scrollPosition < darkHeaderBoundary);
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
  window.addEventListener("resize", refreshHeaderBoundary, { passive: true });
  if (darkHeaderSurface && "ResizeObserver" in window) {
    new ResizeObserver(() => {
      refreshHeaderBoundary();
      updateHeader();
    }).observe(darkHeaderSurface);
  }
  refreshHeaderBoundary();
  updateHeader();

  /* Repository status is progressively enhanced: the verified count in the
     HTML remains usable offline, while a short-lived cache avoids spending a
     public GitHub API request on every page view. */
  const githubStars = document.querySelector("[data-github-stars]");
  const githubStarsLink = document.querySelector("[data-github-stars-link]");

  if (githubStars && githubStarsLink) {
    const cacheKey = "pharos:github-stars:v1";
    const cacheLifetime = 15 * 60 * 1000;
    const compactNumber = new Intl.NumberFormat("en-US", {
      notation: "compact",
      maximumFractionDigits: 1,
    });

    const renderGithubStars = (count) => {
      if (!Number.isFinite(count) || count < 0) return;
      const roundedCount = Math.round(count);
      const starLabel = roundedCount === 1 ? "Star" : "Stars";
      githubStars.textContent = compactNumber.format(roundedCount);
      githubStarsLink.setAttribute(
        "aria-label",
        `查看 Pharos 的 GitHub Stars（当前 ${roundedCount.toLocaleString("en-US")} 个）`,
      );
      githubStarsLink.title = `Pharos 在 GitHub 上有 ${roundedCount.toLocaleString("en-US")} 个 ${starLabel}`;
    };

    let cachedGithubStars = null;
    try {
      const cachedValue = window.localStorage.getItem(cacheKey);
      const parsedValue = cachedValue ? JSON.parse(cachedValue) : null;
      if (
        parsedValue
        && Number.isFinite(parsedValue.count)
        && parsedValue.count >= 0
        && Number.isFinite(parsedValue.fetchedAt)
      ) {
        cachedGithubStars = parsedValue;
        renderGithubStars(parsedValue.count);
      }
    } catch {
      cachedGithubStars = null;
    }

    const cacheIsFresh = cachedGithubStars
      && Date.now() - cachedGithubStars.fetchedAt < cacheLifetime;

    if (!cacheIsFresh && "fetch" in window) {
      const controller = "AbortController" in window ? new AbortController() : null;
      const timeoutId = window.setTimeout(() => controller?.abort(), 4000);

      fetch("https://api.github.com/repos/hyyyyyyz/Pharos", {
        headers: { Accept: "application/vnd.github+json" },
        signal: controller?.signal,
      })
        .then((response) => {
          if (!response.ok) throw new Error(`GitHub API returned ${response.status}`);
          return response.json();
        })
        .then((repository) => {
          const count = Number(repository?.stargazers_count);
          if (!Number.isFinite(count) || count < 0) return;
          renderGithubStars(count);
          try {
            window.localStorage.setItem(
              cacheKey,
              JSON.stringify({ count, fetchedAt: Date.now() }),
            );
          } catch {
            // Private browsing and storage policies must not affect navigation.
          }
        })
        .catch(() => {
          // The verified HTML value or the last cache remains visible offline.
        })
        .finally(() => window.clearTimeout(timeoutId));
    }
  }

  /* Signature interaction: a real source page and its pixel-aligned Chinese
     layer share one magnifying lens. The lens is also the lighthouse beam's
     endpoint, so the visual metaphor remains continuous without faking a PDF. */
  const demo = document.querySelector("[data-translation-demo]");
  const stage = document.querySelector("[data-paper-stage]");
  const attentionPage = stage?.querySelector("[data-attention-page]");
  const attentionOriginal = attentionPage?.querySelector(".attention-paper__original");
  const attentionLens = stage?.querySelector("[data-attention-lens]");
  const attentionTranslation = stage?.querySelector("[data-attention-translation]");

  if (demo && stage && attentionPage && attentionOriginal && attentionLens && attentionTranslation) {
    let stageRect = stage.getBoundingClientRect();
    let stageDocumentLeft = stageRect.left + window.scrollX;
    let stageDocumentTop = stageRect.top + window.scrollY;
    let pageRect = attentionOriginal.getBoundingClientRect();
    let pageOffsetX = pageRect.left - stageRect.left;
    let pageOffsetY = pageRect.top - stageRect.top;
    let lensRadius = attentionLens.getBoundingClientRect().width / 2;
    let lensZoom = pageRect.width < 370 ? 1.78 : 1.56;
    let userUntil = 0;
    let demoVisible = true;
    let lensReady = false;
    let rafId = 0;
    let beamRatio = { x: 0.48, y: 0.69 };
    let beamPosition = {
      x: pageOffsetX + pageRect.width * beamRatio.x,
      y: pageOffsetY + pageRect.height * beamRatio.y,
    };

    const clamp = (value, min, max) => Math.min(max, Math.max(min, value));

    const syncLens = () => {
      if (!pageRect.width || !pageRect.height) return;
      const localX = pageRect.width * beamRatio.x;
      const localY = pageRect.height * beamRatio.y;
      const stageX = pageOffsetX + localX;
      const stageY = pageOffsetY + localY;
      const originX = stageRect.width / 2;
      const originY = stageRect.height - 13;
      const dx = stageX - originX;
      const dy = stageY - originY;
      const angle = Math.atan2(dy, dx) * (180 / Math.PI);
      const distance = Math.hypot(dx, dy);

      beamPosition = { x: stageX, y: stageY };
      stage.style.setProperty("--beam-x", `${stageX.toFixed(1)}px`);
      stage.style.setProperty("--beam-y", `${stageY.toFixed(1)}px`);
      stage.style.setProperty("--beam-angle", `${angle.toFixed(2)}deg`);
      stage.style.setProperty("--beam-distance", `${distance.toFixed(1)}px`);

      attentionTranslation.style.width = `${pageRect.width.toFixed(1)}px`;
      attentionTranslation.style.height = `${pageRect.height.toFixed(1)}px`;
      attentionTranslation.style.left = `${(lensRadius - localX * lensZoom).toFixed(1)}px`;
      attentionTranslation.style.top = `${(lensRadius - localY * lensZoom).toFixed(1)}px`;
      attentionTranslation.style.transform = `scale(${lensZoom.toFixed(2)})`;
    };

    const refreshGeometry = () => {
      stageRect = stage.getBoundingClientRect();
      stageDocumentLeft = stageRect.left + window.scrollX;
      stageDocumentTop = stageRect.top + window.scrollY;
      pageRect = attentionOriginal.getBoundingClientRect();
      pageOffsetX = pageRect.left - stageRect.left;
      pageOffsetY = pageRect.top - stageRect.top;
      lensRadius = attentionLens.getBoundingClientRect().width / 2;
      lensZoom = pageRect.width < 370 ? 1.78 : 1.56;
      syncLens();
    };

    const placeLens = (x, y) => {
      const contentRadius = lensRadius / lensZoom + 2;
      const minX = Math.min(pageRect.width / 2, contentRadius);
      const minY = Math.min(pageRect.height / 2, contentRadius);
      const localX = clamp(
        x - pageOffsetX,
        minX,
        Math.max(minX, pageRect.width - minX),
      );
      const localY = clamp(
        y - pageOffsetY,
        minY,
        Math.max(minY, pageRect.height - minY),
      );
      beamRatio = {
        x: localX / pageRect.width,
        y: localY / pageRect.height,
      };
      syncLens();
    };

    const autoBeam = (time) => {
      if (!demoVisible) {
        rafId = 0;
        return;
      }
      if (time > userUntil) {
        const t = time / 1700;
        const x = pageOffsetX + pageRect.width * (0.47 + Math.sin(t * 0.72) * 0.14);
        const y = pageOffsetY + pageRect.height * (0.7 + Math.sin(t * 0.93 + 1.2) * 0.12);
        placeLens(x, y);
      }
      rafId = requestAnimationFrame(autoBeam);
    };

    const engageAtPointer = (event) => {
      userUntil = performance.now() + 2300;
      demo.classList.add("is-engaged");
      placeLens(
        event.pageX - stageDocumentLeft,
        event.pageY - stageDocumentTop,
      );
    };

    window.addEventListener("resize", refreshGeometry, { passive: true });
    if ("ResizeObserver" in window) {
      const paperResizeObserver = new ResizeObserver(refreshGeometry);
      paperResizeObserver.observe(stage);
      paperResizeObserver.observe(attentionOriginal);
      paperResizeObserver.observe(attentionLens);
    }

    refreshGeometry();

    if (reduceMotion) {
      stage.removeAttribute("tabindex");
      stage.setAttribute("aria-label", "Attention Is All You Need 真实论文页面；放大镜内静态显示中文演示译文");
    } else {
      stage.addEventListener("pointerenter", refreshGeometry, { passive: true });
      stage.addEventListener("pointermove", engageAtPointer, { passive: true });
      stage.addEventListener(
        "pointerdown",
        (event) => {
          stage.focus({ preventScroll: true });
          refreshGeometry();
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
        placeLens(beamPosition.x + movement[0], beamPosition.y + movement[1]);
      });

      if ("IntersectionObserver" in window) {
        const beamObserver = new IntersectionObserver(
          ([entry]) => {
            demoVisible = Boolean(entry?.isIntersecting);
            if (demoVisible) {
              refreshGeometry();
              if (lensReady && !rafId) rafId = requestAnimationFrame(autoBeam);
            } else if (rafId) {
              cancelAnimationFrame(rafId);
              rafId = 0;
            }
          },
          { threshold: 0.01 },
        );
        beamObserver.observe(demo);
      }

      const pauseAutoBeam = () => {
        if (!rafId) return;
        cancelAnimationFrame(rafId);
        rafId = 0;
      };
      window.addEventListener("pagehide", pauseAutoBeam);
      window.addEventListener("pageshow", (event) => {
        if (!event.persisted || !demoVisible || rafId) return;
        refreshGeometry();
        if (lensReady) rafId = requestAnimationFrame(autoBeam);
      });
    }

    const waitForImage = (image) => {
      if (image.complete) {
        if (!image.naturalWidth) return Promise.reject(new Error("Image failed to load"));
        return typeof image.decode === "function"
          ? image.decode().catch(() => undefined)
          : Promise.resolve();
      }
      return new Promise((resolve, reject) => {
        image.addEventListener("load", resolve, { once: true });
        image.addEventListener("error", reject, { once: true });
      });
    };

    Promise.all([
      waitForImage(attentionOriginal),
      waitForImage(attentionTranslation),
    ])
      .then(() => {
        lensReady = true;
        demo.classList.add("is-lens-ready");
        refreshGeometry();
        if (!reduceMotion && demoVisible && !rafId) {
          rafId = requestAnimationFrame(autoBeam);
        }
      })
      .catch(() => {
        stage.setAttribute("aria-label", "Attention Is All You Need 真实论文页面；中文译文演示图暂时无法加载");
      });
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
      scene.inert = !active;
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
    let posterRect = posterFrame.getBoundingClientRect();
    const refreshPosterRect = () => {
      posterRect = posterFrame.getBoundingClientRect();
    };
    posterFrame.addEventListener("pointerenter", refreshPosterRect, { passive: true });
    posterFrame.addEventListener(
      "pointermove",
      (event) => {
        const x = (event.clientX - posterRect.left) / posterRect.width - 0.5;
        const y = (event.clientY - posterRect.top) / posterRect.height - 0.5;
        posterImage.style.transform = `translate3d(${(x * -9).toFixed(1)}px, ${(y * -7).toFixed(1)}px, 0) scale(1.018)`;
      },
      { passive: true },
    );
    posterFrame.addEventListener("pointerleave", () => {
      posterImage.style.transform = "";
    });
    if ("ResizeObserver" in window) new ResizeObserver(refreshPosterRect).observe(posterFrame);
  }

  /* The three research signals remain useful even without WebGL: they explain
     the workflow in HTML, while the scene listens for the same custom events
     and turns the lighthouse toward the requested node when it is available. */
  const beaconHero = document.querySelector("[data-beacon-hero]");
  const beaconSignalControls = [...document.querySelectorAll("[data-beacon-signal]")];
  const beaconDetail = beaconHero?.querySelector("[data-beacon-detail]");
  const beaconChapter = beaconHero?.querySelector("[data-beacon-chapter]");
  const beaconHeadline = beaconHero?.querySelector("[data-beacon-headline]");
  const signalCopy = [
    "把模糊的想法整理为可以检索、讨论与验证的研究问题。",
    "围绕问题汇聚论文，在方法、实验与结论之间建立证据链。",
    "让证据继续流向研究判断、实验计划与最终成果。",
  ];
  const storyCopy = [
    ["PRODUCT VISION · 01 / ORIENT", "研究航线正在展开"],
    ["PRODUCT VISION · 02 / QUESTION", "让想法成为可以探索的问题"],
    ["PRODUCT VISION · 03 / EVIDENCE", "让相关证据在同一上下文中汇聚"],
    ["PRODUCT VISION · 04 / OUTCOME", "让研究路径向实验与论文延伸"],
    ["CURRENT FOUNDATION · 05 / READING", "第一束光，落向当前可用的深度阅读"],
  ];

  const setSignalPresentation = (index, source = "story") => {
    beaconSignalControls.forEach((control, controlIndex) => {
      const active = controlIndex === index;
      control.classList.toggle("is-active", active);
      control.setAttribute("aria-pressed", String(active && source === "pinned"));
    });
    if (beaconDetail) {
      beaconDetail.textContent = index >= 0
        ? signalCopy[index]
        : "从提出问题开始，让证据、判断与研究进展始终留在同一条航线上。";
    }
  };

  const setStoryPresentation = (index) => {
    const copy = storyCopy[index] ?? storyCopy[0];
    if (beaconChapter) beaconChapter.textContent = copy[0];
    if (beaconHeadline) beaconHeadline.textContent = copy[1];
  };

  if (beaconHero && beaconSignalControls.length) {
    beaconSignalControls.forEach((control, index) => {
      const requestSignal = (mode) => {
        setSignalPresentation(index, mode === "pin" ? "pinned" : "preview");
        beaconHero.dispatchEvent(new CustomEvent("pharos:signal-request", {
          detail: { index, mode },
        }));
      };
      const releaseSignal = () => {
        beaconHero.dispatchEvent(new CustomEvent("pharos:signal-release", {
          detail: { index, mode: "preview" },
        }));
      };

      control.addEventListener("pointerenter", (event) => {
        if (event.pointerType !== "touch") requestSignal("preview");
      });
      control.addEventListener("pointerleave", (event) => {
        if (event.pointerType !== "touch") releaseSignal();
      });
      control.addEventListener("focus", () => requestSignal("preview"));
      control.addEventListener("blur", releaseSignal);
      control.addEventListener("click", () => requestSignal("pin"));
    });

    beaconHero.addEventListener("pharos:signal-active", (event) => {
      const { index = -1, source = "story" } = event.detail ?? {};
      setSignalPresentation(index, source);
    });
    beaconHero.addEventListener("pharos:story-active", (event) => {
      setStoryPresentation(event.detail?.index ?? 0);
    });
    setStoryPresentation(0);
    setSignalPresentation(-1);
  }

  /* The cinematic lighthouse is progressive enhancement. The existing HTML and
     CSS fallback remain complete if WebGL, the dynamic chunk, or the GPU fails. */
  const beaconCanvas = beaconHero?.querySelector("[data-lighthouse-canvas]");
  const beaconSignalGroup = beaconHero?.querySelector(".hero__signals");
  const beaconStatus = beaconHero?.querySelector("[data-beacon-status]");
  const saveData = navigator.connection?.saveData === true;

  let lighthouseDestroy = null;
  let lighthouseLaunchId = 0;
  const setStaticBeacon = () => {
    if (!beaconHero) return;
    beaconHero.classList.remove("is-scene-ready", "is-beam-guided");
    beaconHero.dataset.sceneMode = "static";
    if (beaconChapter) beaconChapter.textContent = "STATIC SCENE · ACCESSIBLE FALLBACK";
    if (beaconHeadline) beaconHeadline.textContent = "静态灯塔场景已就绪";
    if (beaconStatus) beaconStatus.textContent = "已启用静态场景 · 下方仍可完整查看产品演示";
    beaconSignalGroup?.setAttribute("aria-label", "查看研究工作流节点");
  };

  const launchLighthouse = () => {
    if (!beaconHero || !beaconCanvas || saveData || reduceMotionQuery.matches) {
      setStaticBeacon();
      return;
    }
    const launchId = ++lighthouseLaunchId;
    beaconHero.dataset.sceneMode = "loading";
    import("./lighthouse.js")
      .then(async ({ initLighthouseScene }) => {
        const destroy = await initLighthouseScene({ root: beaconHero, canvas: beaconCanvas });
        if (launchId !== lighthouseLaunchId || reduceMotionQuery.matches) {
          destroy?.();
          setStaticBeacon();
          return;
        }
        lighthouseDestroy = destroy;
        beaconHero.dataset.sceneMode = "interactive";
        beaconSignalGroup?.setAttribute("aria-label", "选择灯塔要连接的研究节点");
      })
      .catch(setStaticBeacon);
  };

  if (beaconHero && beaconCanvas) {
    launchLighthouse();
    reduceMotionQuery.addEventListener?.("change", (event) => {
      lighthouseLaunchId += 1;
      if (event.matches) {
        lighthouseDestroy?.();
        lighthouseDestroy = null;
        setStaticBeacon();
      } else {
        launchLighthouse();
      }
    });
  }
})();
