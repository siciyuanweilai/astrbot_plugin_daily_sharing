import { IMAGE_CACHE_LIMIT } from "./constants.js?v=20260609-media-flat";
import { text } from "./format.js?v=20260609-format";

export function createMediaImageController({
  state,
  elements: el,
  apiPost,
  setNotice,
} = {}) {
  function mediaImageCacheKey(item, mode = "view") {
    const id = text(item?.id).trim();
    if (id) return `${mode}:id:${id}`;
    const ref = text(item?.media_url || item?.media_path || item?.preview_url).trim();
    return ref ? `${mode}:ref:${ref}` : "";
  }

  function revokeImageSource(source) {
    if (source?.objectUrl) URL.revokeObjectURL(source.objectUrl);
  }

  function clearLoadState() {
    state.mediaLightboxViewSource = null;
    state.mediaLightboxViewPromise = null;
  }

  function getCachedImageSource(cacheKey) {
    if (!cacheKey || !state.mediaImageCache.has(cacheKey)) return null;
    const entry = state.mediaImageCache.get(cacheKey);
    state.mediaImageCache.delete(cacheKey);
    state.mediaImageCache.set(cacheKey, entry);
    return entry.source;
  }

  function setCachedImageSource(cacheKey, source) {
    if (!cacheKey || !source) return;
    const oldEntry = state.mediaImageCache.get(cacheKey);
    if (oldEntry?.source?.objectUrl !== source.objectUrl) {
      revokeImageSource(oldEntry?.source);
    }
    state.mediaImageCache.delete(cacheKey);
    state.mediaImageCache.set(cacheKey, { source });

    while (state.mediaImageCache.size > IMAGE_CACHE_LIMIT) {
      const [oldestKey, oldestEntry] = state.mediaImageCache.entries().next().value;
      revokeImageSource(oldestEntry?.source);
      state.mediaImageCache.delete(oldestKey);
    }
  }

  function clearMemoryCache() {
    for (const entry of state.mediaImageCache.values()) {
      revokeImageSource(entry?.source);
    }
    state.mediaImageCache.clear();
  }

  function isCurrentLoad(token) {
    return token == null || (!el.mediaLightbox.hidden && state.mediaLightboxToken === token);
  }

  function updateLoadingClass() {
    el.mediaLightboxImage.classList.toggle(
      "loading-full",
      state.mediaLightboxViewLoading,
    );
  }

  function resetViewState() {
    state.mediaLightboxViewLoading = false;
    state.mediaLightboxViewLoaded = false;
    updateLoadingClass();
  }

  function fitLightboxSize(width, height) {
    width = Number(width);
    height = Number(height);
    if (!Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) {
      return null;
    }
    const maxWidth = Math.max(320, window.innerWidth - 16);
    const maxHeight = Math.max(240, window.innerHeight - 16);
    const scale = Math.min(maxWidth / width, maxHeight / height);
    return {
      width: Math.max(1, Math.round(width * scale)),
      height: Math.max(1, Math.round(height * scale)),
    };
  }

  function imageSize(image) {
    if (!image) return null;
    return {
      width: image.naturalWidth || 0,
      height: image.naturalHeight || 0,
    };
  }

  function setLightboxSize(size) {
    const fitted = fitLightboxSize(size?.width, size?.height);
    if (!fitted) {
      el.mediaLightbox.style.removeProperty("--media-lightbox-width");
      el.mediaLightbox.style.removeProperty("--media-lightbox-height");
      return;
    }
    el.mediaLightbox.style.setProperty("--media-lightbox-width", `${fitted.width}px`);
    el.mediaLightbox.style.setProperty("--media-lightbox-height", `${fitted.height}px`);
  }

  async function preloadSource(src, token, objectUrl = "", blob = null, errorMessage = "图片加载失败") {
    if (!src) return null;
    if (!isCurrentLoad(token)) {
      if (objectUrl) URL.revokeObjectURL(objectUrl);
      return null;
    }
    if (typeof Image !== "function") {
      return { src, objectUrl, blob };
    }

    return new Promise((resolve, reject) => {
      const image = new Image();
      image.decoding = "async";
      image.loading = "eager";
      image.fetchPriority = token == null ? "low" : "high";
      image.onload = async () => {
        if (typeof image.decode === "function") {
          await image.decode().catch(() => {});
        }
        if (!isCurrentLoad(token)) {
          if (objectUrl) URL.revokeObjectURL(objectUrl);
          resolve(null);
          return;
        }
        resolve({ src, objectUrl, blob, image });
      };
      image.onerror = () => {
        if (objectUrl) URL.revokeObjectURL(objectUrl);
        reject(new Error(errorMessage));
      };
      image.src = src;
    });
  }

  async function buildViewSource(data, token) {
    if (data.view_url) {
      return preloadSource(data.view_url, token, "", null, "查看图加载失败");
    }
    throw new Error("查看图数据无效");
  }

  async function loadViewSource(item, token) {
    const cacheKey = mediaImageCacheKey(item);
    const cachedSource = getCachedImageSource(cacheKey);
    if (cachedSource) return cachedSource;

    if (!isCurrentLoad(token)) return null;
    let source = null;
    if (item?.id) {
      const data = await apiPost("page/media/view", { history_id: item.id });
      source = await buildViewSource(data, token);
    } else {
      const viewUrl = text(item?.media_url || item?.preview_url || state.mediaLightboxPreviewUrl).trim();
      source = await preloadSource(viewUrl, token, "", null, "查看图加载失败");
    }
    if (source) setCachedImageSource(cacheKey, source);
    return source;
  }

  function currentViewSource(token) {
    if (state.mediaLightboxViewSource) {
      return Promise.resolve(state.mediaLightboxViewSource);
    }
    if (!state.mediaLightboxViewPromise) {
      state.mediaLightboxViewPromise = loadViewSource(state.mediaLightboxItem, token)
        .then((source) => {
          if (source && state.mediaLightboxToken === token) {
            state.mediaLightboxViewSource = source;
          }
          return source;
        })
        .catch((error) => {
          if (state.mediaLightboxToken === token) {
            state.mediaLightboxViewPromise = null;
          }
          throw error;
        });
    }
    return state.mediaLightboxViewPromise;
  }

  async function loadCurrentView() {
    const item = state.mediaLightboxItem;
    if (!item || state.mediaLightboxViewLoaded) return;
    const token = state.mediaLightboxToken;
    state.mediaLightboxViewLoading = true;
    updateLoadingClass();
    try {
      const source = await currentViewSource(token);
      if (!source) return;
      if (!el.mediaLightbox.hidden && state.mediaLightboxToken === token) {
        el.mediaLightboxImage.src = source.src;
        state.mediaLightboxViewLoaded = true;
        el.mediaLightbox.classList.add("image-preview-mode");
      }
    } catch (error) {
      setNotice(error.message || "查看图加载失败，已显示预览图。", "error");
    } finally {
      if (state.mediaLightboxToken === token) {
        state.mediaLightboxViewLoading = false;
        updateLoadingClass();
      }
    }
  }

  return {
    clearLoadState,
    clearMemoryCache,
    imageSize,
    loadCurrentView,
    resetViewState,
    setLightboxSize,
  };
}
