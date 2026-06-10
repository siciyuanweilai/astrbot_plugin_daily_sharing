import {
  LIGHTBOX_ANIMATION_MS,
  MEDIA_INITIAL_LIMIT,
} from "./constants.js?v=20260609-media-flat";
import { createMediaGalleryController } from "./gallery.js?v=20260610-refactor";
import { createMediaImageController } from "./image.js?v=20260610-structure";
import { createOverlayController } from "./overlay.js?v=20260609-media-flat";
import { createLightboxVideoController } from "./video.js?v=20260609-media-flat";

export { MEDIA_INITIAL_LIMIT };

export function createMediaUi({
  state,
  elements: el,
  apiGet,
  apiPost,
  setNotice,
  syncSweetSelect,
} = {}) {
  const overlay = createOverlayController({ state, elements: el });
  const video = createLightboxVideoController({ state, elements: el });
  const image = createMediaImageController({ state, elements: el, apiPost, setNotice });

  function openMediaLightbox(src, _caption, kind = "image", imageSize = null) {
    if (!src) return 0;
    const wasHidden = el.mediaLightbox.hidden;
    if (state.mediaLightboxCloseTimer) {
      window.clearTimeout(state.mediaLightboxCloseTimer);
      state.mediaLightboxCloseTimer = 0;
    }
    state.mediaLightboxToken += 1;
    image.clearLoadState();
    el.mediaLightbox.classList.remove("is-closing");
    const isVideo = kind === "video";
    el.mediaLightbox.classList.toggle("image-mode", !isVideo);
    el.mediaLightbox.classList.toggle("video-mode", isVideo);
    el.mediaLightbox.classList.remove("video-is-playing");
    video.resetLightboxVideoControls();
    el.mediaLightbox.classList.toggle("image-preview-mode", !isVideo);
    el.mediaLightboxImage.hidden = isVideo;
    el.mediaLightboxVideo.hidden = !isVideo;
    el.mediaLightboxCloseSurface.hidden = !isVideo;
    el.mediaLightboxPlayButton.hidden = !isVideo;
    el.mediaLightboxControls.hidden = !isVideo;
    el.mediaLightboxPlayButton.setAttribute("aria-label", "播放视频");
    el.mediaLightboxPlayButton.setAttribute("aria-pressed", "false");
    video.resetLightboxVideoProgress();
    image.setLightboxSize(isVideo ? null : imageSize);
    state.mediaLightboxItem = null;
    state.mediaLightboxPreviewUrl = isVideo ? "" : src;
    image.resetViewState();
    if (isVideo) {
      el.mediaLightboxVideo.controls = false;
      el.mediaLightboxVideo.removeAttribute("controls");
      video.applyStoredVideoMuted();
      el.mediaLightboxVideo.src = src;
      el.mediaLightboxVideo.load();
      video.syncLightboxVideoProgress({ force: true });
    } else {
      el.mediaLightboxVideo.pause();
      el.mediaLightboxVideo.removeAttribute("src");
      el.mediaLightboxVideo.load();
      el.mediaLightboxImage.src = src;
    }
    document.documentElement.classList.add("media-lightbox-open");
    document.body.classList.add("media-lightbox-open");
    el.mediaLightbox.hidden = false;
    if (wasHidden) overlay.lockPageScroll();
    el.mediaLightbox.focus();
    return state.mediaLightboxToken;
  }

  function openImagePreviewLightbox(item, previewUrl, previewImage = null) {
    const token = openMediaLightbox(previewUrl, "", "image", image.imageSize(previewImage));
    if (!token) return;
    state.mediaLightboxItem = item;
    image.loadCurrentView();
  }

  function closeMediaLightbox() {
    if (el.mediaLightbox.hidden || el.mediaLightbox.classList.contains("is-closing")) return;
    state.mediaLightboxToken += 1;
    el.mediaLightbox.classList.add("is-closing");
    video.clearLightboxVideoControlsTimer();
    video.clearLightboxCloseSuppression();
    state.mediaLightboxControlsRevealLocked = false;
    state.mediaLightboxControlsHotzoneSuppressed = false;
    state.mediaLightboxControlsLeaveDirection = "";
    state.mediaLightboxControlsPointerY = null;
    image.clearLoadState();
    image.resetViewState();
    el.mediaLightboxVideo.pause();
    state.mediaLightboxCloseTimer = window.setTimeout(() => {
      state.mediaLightboxCloseTimer = 0;
      el.mediaLightbox.hidden = true;
      el.mediaLightbox.classList.remove("is-closing", "image-mode", "video-mode", "video-is-playing", "video-controls-visible", "image-preview-mode");
      document.documentElement.classList.remove("media-lightbox-open");
      document.body.classList.remove("media-lightbox-open");
      el.mediaLightboxCloseSurface.hidden = true;
      el.mediaLightboxPlayButton.hidden = true;
      el.mediaLightboxControls.hidden = true;
      video.resetLightboxVideoProgress();
      el.mediaLightboxImage.removeAttribute("src");
      el.mediaLightboxVideo.removeAttribute("src");
      el.mediaLightboxVideo.load();
      state.mediaLightboxItem = null;
      state.mediaLightboxPreviewUrl = "";
      image.setLightboxSize(null);
      overlay.unlockPageScroll();
    }, LIGHTBOX_ANIMATION_MS);
  }

  function handleLightboxCloseClick(event) {
    if (video.consumeSuppressedCloseClick(event)) return;
    closeMediaLightbox();
  }

  const gallery = createMediaGalleryController({
    state,
    elements: el,
    apiGet,
    setNotice,
    syncSweetSelect,
    openImagePreviewLightbox,
    openMediaLightbox,
    openTextLightbox: overlay.openTextLightbox,
  });

  function bindMediaEvents() {
    gallery.bindGalleryEvents();
    el.mediaLightbox.addEventListener("click", handleLightboxCloseClick);
    el.mediaLightboxContent.addEventListener("pointermove", video.handleLightboxContentPointerMove);
    el.mediaLightboxCloseSurface.addEventListener("pointerdown", video.handleLightboxCloseSurfacePointerDown);
    el.mediaLightboxCloseSurface.addEventListener("click", handleLightboxCloseClick);
    el.mediaLightboxPlayButton.addEventListener("click", video.toggleLightboxVideoPlayback);
    el.mediaLightboxControls.addEventListener("click", video.stopMediaControlClose);
    el.mediaLightboxControls.addEventListener("pointerenter", video.handleLightboxControlsPointerEnter);
    el.mediaLightboxControls.addEventListener("pointerleave", video.handleLightboxControlsPointerLeave);
    const lightboxProgressTarget = el.mediaLightboxProgressTrack || el.mediaLightboxProgress;
    lightboxProgressTarget.addEventListener("pointerdown", video.beginLightboxProgressSeek);
    lightboxProgressTarget.addEventListener("touchstart", video.beginLightboxProgressSeek, { passive: false });
    lightboxProgressTarget.addEventListener("pointermove", video.handleLightboxProgressTouchMove);
    lightboxProgressTarget.addEventListener("touchmove", video.handleLightboxProgressTouchMove, { passive: false });
    lightboxProgressTarget.addEventListener("pointerup", video.commitLightboxProgressSeek);
    lightboxProgressTarget.addEventListener("pointercancel", video.commitLightboxProgressSeek);
    lightboxProgressTarget.addEventListener("touchend", video.commitLightboxProgressSeek);
    el.mediaLightboxProgress.addEventListener("input", video.handleLightboxProgressInput);
    el.mediaLightboxProgress.addEventListener("change", video.commitLightboxProgressSeek);
    el.mediaLightboxProgress.addEventListener("blur", video.commitLightboxProgressSeek);
    el.mediaLightboxMuteButton.addEventListener("click", video.toggleLightboxVideoMuted);
    el.mediaLightboxVideo.addEventListener("play", video.syncLightboxVideoControls);
    el.mediaLightboxVideo.addEventListener("pause", video.syncLightboxVideoControls);
    el.mediaLightboxVideo.addEventListener("ended", video.syncLightboxVideoControls);
    el.mediaLightboxVideo.addEventListener("loadedmetadata", () => video.syncLightboxVideoProgress({ force: true }));
    el.mediaLightboxVideo.addEventListener("durationchange", () => video.syncLightboxVideoProgress({ force: true }));
    el.mediaLightboxVideo.addEventListener("timeupdate", video.syncLightboxVideoProgress);
    el.mediaLightboxVideo.addEventListener("seeked", () => video.syncLightboxVideoProgress({ force: true }));
    el.mediaLightboxVideo.addEventListener("volumechange", video.syncLightboxVideoMuteButton);
    el.textLightbox.addEventListener("click", overlay.handleTextLightboxClick);
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !el.mediaLightbox.hidden) closeMediaLightbox();
      if (event.key === "Escape" && !el.textLightbox.hidden) overlay.closeTextLightbox();
    });
  }

  return {
    applyMediaPage: gallery.applyMediaPage,
    bindMediaEvents,
    clearImageMemoryCache: image.clearMemoryCache,
    isDefaultMediaFilter: gallery.isDefaultMediaFilter,
    reloadMediaPage: gallery.reloadMediaPage,
    renderMedia: gallery.renderMedia,
    renderMediaFilters: gallery.renderMediaFilters,
    resetMediaPage: gallery.resetMediaPage,
    syncDefaultMediaFromStatus: gallery.syncDefaultMediaFromStatus,
    updateMediaButton: gallery.updateMediaButton,
  };
}
