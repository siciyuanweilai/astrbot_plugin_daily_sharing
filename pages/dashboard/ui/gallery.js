import {
  emptyNode,
  replaceChildren,
  text,
} from "./format.js?v=20260609-format";
import {
  MEDIA_INITIAL_LIMIT,
  MEDIA_LOAD_STEP,
  MEDIA_MAX_LIMIT,
} from "./constants.js?v=20260609-media-flat";
import { createMediaItem, mediaKind } from "./items.js?v=20260609-media-flat";

export function createMediaGalleryController({
  state,
  elements: el,
  apiGet,
  setNotice,
  syncSweetSelect,
  openImagePreviewLightbox,
  openMediaLightbox,
  openTextLightbox,
} = {}) {
  function updateMediaButton() {
    if (state.mediaLoading) {
      el.mediaButton.textContent = "加载中...";
      el.mediaButton.disabled = true;
      return;
    }
    if (!state.mediaHasMore) {
      el.mediaButton.textContent = "没有更多了";
      el.mediaButton.disabled = true;
      return;
    }
    el.mediaButton.textContent = "加载更多";
    el.mediaButton.disabled = false;
  }

  function isDefaultMediaFilter() {
    return state.mediaKindFilter === "all" && state.mediaTypeFilter === "all";
  }

  function mediaPageParams(limit) {
    return {
      limit,
      kind: state.mediaKindFilter,
      type: state.mediaTypeFilter,
      _ts: Date.now(),
    };
  }

  function applyMediaPage(data = {}) {
    const items = Array.isArray(data.items)
      ? data.items
      : Array.isArray(data.media)
        ? data.media
        : [];
    const limit = Number(data.limit ?? data.media_limit);
    const hasMore = data.has_more ?? data.media_has_more;
    state.media = items;
    state.mediaLimit = Number.isFinite(limit) && limit > 0
      ? limit
      : Math.max(MEDIA_INITIAL_LIMIT, items.length);
    const moreAvailable = typeof hasMore === "boolean"
      ? hasMore
      : items.length >= state.mediaLimit;
    state.mediaHasMore = moreAvailable && state.mediaLimit < MEDIA_MAX_LIMIT;
    state.mediaLoaded = true;
  }

  function mediaItemKey(item = {}) {
    const id = text(item.id).trim();
    if (id) return `id:${id}`;
    return [
      text(item.timestamp).trim(),
      text(item.target_id).trim(),
      text(item.type).trim(),
      text(item.media_path || item.media_url).trim(),
      text(item.content).trim(),
    ].join("|");
  }

  function mediaListRenderSignature(media = []) {
    return [
      state.mediaKindFilter,
      state.mediaTypeFilter,
      media.map((item) => [
        mediaItemKey(item),
        mediaKind(item),
        text(item.preview_url).trim(),
        text(item.media_url).trim(),
        text(item.media_path).trim(),
        text(item.timestamp).trim(),
        text(item.target_label || item.target_id).trim(),
        text(item.type).trim(),
        text(item.content).trim(),
      ].join("\x1f")).join("\x1e"),
    ].join("\x1d");
  }

  function syncDefaultMediaFromStatus(status = {}) {
    if (!isDefaultMediaFilter()) return;
    if (!state.mediaLoaded || state.mediaLimit <= MEDIA_INITIAL_LIMIT) {
      applyMediaPage(status);
      return;
    }

    const latest = Array.isArray(status.media) ? status.media : [];
    const merged = new Map();
    for (const item of [...latest, ...state.media]) {
      const key = mediaItemKey(item);
      if (key && !merged.has(key)) merged.set(key, item);
    }
    const limit = Math.max(MEDIA_INITIAL_LIMIT, state.mediaLimit);
    state.media = [...merged.values()].slice(0, limit);
    state.mediaHasMore = Boolean(status.media_has_more) || state.mediaHasMore;
    state.mediaLoaded = true;
  }

  function resetMediaPage() {
    state.mediaRequestSeq += 1;
    state.media = [];
    state.mediaLoaded = false;
    state.mediaLimit = MEDIA_INITIAL_LIMIT;
    state.mediaHasMore = true;
    state.mediaLoading = false;
    state.mediaRenderSignature = "";
  }

  function renderMediaFilters() {
    for (const button of el.mediaKindButtons) {
      const active = button.dataset.mediaKind === state.mediaKindFilter;
      button.classList.toggle("active", active);
      button.setAttribute("aria-pressed", active ? "true" : "false");
    }
    if (el.mediaTypeFilter.value !== state.mediaTypeFilter) {
      el.mediaTypeFilter.value = state.mediaTypeFilter;
    }
    syncSweetSelect(el.mediaTypeFilter);
  }

  function renderMedia({ force = false } = {}) {
    const media = state.mediaLoaded ? state.media : state.status?.media || [];
    const signature = mediaListRenderSignature(media);
    if (!force && signature === state.mediaRenderSignature && el.mediaList.childElementCount) {
      updateMediaButton();
      return;
    }
    state.mediaRenderSignature = signature;
    if (!media.length) {
      replaceChildren(el.mediaList, [emptyNode("暂无动态记录")]);
      updateMediaButton();
      return;
    }
    replaceChildren(el.mediaList, media.map((item) => createMediaItem(item, {
      openImagePreviewLightbox,
      openMediaLightbox,
      openTextLightbox,
    })));
    updateMediaButton();
  }

  async function reloadMediaPage({ quiet = false } = {}) {
    resetMediaPage();
    const requestSeq = state.mediaRequestSeq;
    state.mediaLoaded = true;
    state.mediaLoading = true;
    renderMediaFilters();
    renderMedia();
    try {
      const data = await apiGet("page/media", mediaPageParams(MEDIA_INITIAL_LIMIT));
      if (requestSeq !== state.mediaRequestSeq) return;
      applyMediaPage(data);
      renderMedia({ force: true });
      if (!quiet) setNotice("");
    } catch (error) {
      if (requestSeq === state.mediaRequestSeq) {
        setNotice(error.message || "动态加载失败", "error");
      }
    } finally {
      if (requestSeq === state.mediaRequestSeq) {
        state.mediaLoading = false;
        updateMediaButton();
      }
    }
  }

  async function loadMoreMedia() {
    if (state.mediaLoading || !state.mediaHasMore) return;
    if (state.mediaLimit >= MEDIA_MAX_LIMIT) {
      state.mediaHasMore = false;
      updateMediaButton();
      return;
    }
    state.mediaLoading = true;
    const requestSeq = state.mediaRequestSeq + 1;
    state.mediaRequestSeq = requestSeq;
    updateMediaButton();
    try {
      const previousCount = state.media.length;
      const nextLimit = state.mediaLimit < MEDIA_LOAD_STEP
        ? MEDIA_LOAD_STEP
        : Math.min(state.mediaLimit + MEDIA_LOAD_STEP, MEDIA_MAX_LIMIT);
      const data = await apiGet("page/media", mediaPageParams(nextLimit));
      if (requestSeq !== state.mediaRequestSeq) return;
      applyMediaPage(data);
      if (state.media.length <= previousCount || state.media.length < nextLimit) {
        state.mediaHasMore = false;
      }
      renderMedia({ force: true });
    } catch (error) {
      if (requestSeq === state.mediaRequestSeq) {
        setNotice(error.message || "动态加载失败", "error");
      }
    } finally {
      if (requestSeq === state.mediaRequestSeq) {
        state.mediaLoading = false;
        updateMediaButton();
      }
    }
  }

  function bindGalleryEvents() {
    el.mediaButton.addEventListener("click", loadMoreMedia);
    el.mediaTypeFilter.addEventListener("change", () => {
      state.mediaTypeFilter = el.mediaTypeFilter.value || "all";
      reloadMediaPage();
    });
    for (const button of el.mediaKindButtons) {
      button.addEventListener("click", () => {
        const nextKind = button.dataset.mediaKind || "all";
        if (nextKind === state.mediaKindFilter) return;
        state.mediaKindFilter = nextKind;
        reloadMediaPage();
      });
    }
  }

  return {
    applyMediaPage,
    bindGalleryEvents,
    isDefaultMediaFilter,
    reloadMediaPage,
    renderMedia,
    renderMediaFilters,
    resetMediaPage,
    syncDefaultMediaFromStatus,
    updateMediaButton,
  };
}
