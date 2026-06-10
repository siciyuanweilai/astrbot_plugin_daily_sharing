import {
  VIDEO_CONTROLS_HIDE_DELAY_MS,
  VIDEO_CONTROLS_LEAVE_HIDE_DELAY_MS,
  VIDEO_CONTROLS_REVEAL_HIDE_DELAY_MS,
  VIDEO_CONTROLS_REVEAL_PADDING,
  VIDEO_CONTROLS_TOUCH_REVEAL_HIDE_DELAY_MS,
  VIDEO_CONTROLS_TOUCH_SEEK_HIDE_DELAY_MS,
  VIDEO_MUTED_STORAGE_KEY,
} from "./constants.js?v=20260609-media-flat";

export function createLightboxVideoController({ state, elements: el } = {}) {
  function readLocalVideoMuted() {
    try {
      return localStorage.getItem(VIDEO_MUTED_STORAGE_KEY) === "on";
    } catch {
      return false;
    }
  }

  function writeLocalVideoMuted(muted) {
    try {
      localStorage.setItem(VIDEO_MUTED_STORAGE_KEY, muted ? "on" : "off");
    } catch {
      // 忽略本地存储失败；当前页面状态仍会更新。
    }
  }

  function videoTimeLabel(seconds) {
    if (!Number.isFinite(seconds) || seconds <= 0) return "0:00";
    const totalSeconds = Math.floor(seconds);
    const hours = Math.floor(totalSeconds / 3600);
    const minutes = Math.floor((totalSeconds % 3600) / 60);
    const restSeconds = totalSeconds % 60;
    const paddedSeconds = String(restSeconds).padStart(2, "0");
    if (!hours) return `${minutes}:${paddedSeconds}`;
    return `${hours}:${String(minutes).padStart(2, "0")}:${paddedSeconds}`;
  }

  function lightboxVideoDuration() {
    const duration = el.mediaLightboxVideo.duration;
    return Number.isFinite(duration) && duration > 0 ? duration : 0;
  }

  function lightboxVideoProgressRatio(currentTime = el.mediaLightboxVideo.currentTime) {
    const duration = lightboxVideoDuration();
    return duration ? Math.max(0, Math.min(1, currentTime / duration)) : 0;
  }

  function setLightboxVideoProgress(ratio) {
    const value = Math.round(Math.max(0, Math.min(1, ratio)) * Number(el.mediaLightboxProgress.max || 1000));
    el.mediaLightboxProgress.value = String(value);
    el.mediaLightboxProgress.style.setProperty("--media-progress-percent", `${value / 10}%`);
  }

  function syncLightboxVideoProgress({ force = false } = {}) {
    const duration = lightboxVideoDuration();
    const currentTime = Math.max(0, el.mediaLightboxVideo.currentTime || 0);
    const canSeek = duration > 0;
    el.mediaLightboxProgress.disabled = !canSeek;
    el.mediaLightboxControls?.classList.toggle("is-disabled", !canSeek);
    el.mediaLightboxCurrentTime.textContent = videoTimeLabel(currentTime);
    el.mediaLightboxDuration.textContent = videoTimeLabel(duration);
    if (!state.mediaLightboxProgressDragging || force) {
      setLightboxVideoProgress(lightboxVideoProgressRatio(currentTime));
    }
  }

  function resetLightboxVideoProgress() {
    state.mediaLightboxProgressDragging = false;
    el.mediaLightboxProgress.disabled = true;
    el.mediaLightboxProgress.value = "0";
    el.mediaLightboxProgress.style.setProperty("--media-progress-percent", "0%");
    el.mediaLightboxCurrentTime.textContent = "0:00";
    el.mediaLightboxDuration.textContent = "0:00";
    el.mediaLightboxControls?.classList.add("is-disabled");
  }

  function syncLightboxVideoMuteButton() {
    const muted = Boolean(el.mediaLightboxVideo.muted);
    el.mediaLightboxMuteButton.classList.toggle("is-muted", muted);
    el.mediaLightboxMuteButton.setAttribute("aria-pressed", muted ? "true" : "false");
    el.mediaLightboxMuteButton.setAttribute("aria-label", muted ? "取消静音" : "静音");
  }

  function setLightboxVideoMuted(muted, { persist = false } = {}) {
    el.mediaLightboxVideo.muted = muted;
    syncLightboxVideoMuteButton();
    if (persist) writeLocalVideoMuted(muted);
  }

  function applyStoredVideoMuted() {
    setLightboxVideoMuted(readLocalVideoMuted());
  }

  function isLightboxVideoPlaying() {
    return !el.mediaLightboxVideo.paused && !el.mediaLightboxVideo.ended;
  }

  function clearLightboxVideoControlsTimer() {
    if (!state.mediaLightboxControlTimer) return;
    window.clearTimeout(state.mediaLightboxControlTimer);
    state.mediaLightboxControlTimer = 0;
  }

  function setLightboxVideoControlsVisible(visible, { lockReveal = false } = {}) {
    const nextVisible = Boolean(visible);
    el.mediaLightbox.classList.toggle("video-controls-visible", nextVisible);
    if (!nextVisible && lockReveal && isLightboxVideoPlaying()) {
      state.mediaLightboxControlsRevealLocked = true;
    }
  }

  function scheduleLightboxVideoControlsHide(delay = VIDEO_CONTROLS_REVEAL_HIDE_DELAY_MS, options = {}) {
    clearLightboxVideoControlsTimer();
    if (!isLightboxVideoPlaying() || state.mediaLightboxProgressDragging) return;
    state.mediaLightboxControlTimer = window.setTimeout(() => {
      state.mediaLightboxControlTimer = 0;
      setLightboxVideoControlsVisible(false, options);
    }, delay);
  }

  function showLightboxVideoControls({ autoHide = false, hideDelay = VIDEO_CONTROLS_REVEAL_HIDE_DELAY_MS } = {}) {
    clearLightboxVideoControlsTimer();
    state.mediaLightboxControlsRevealLocked = false;
    setLightboxVideoControlsVisible(true);
    if (autoHide) scheduleLightboxVideoControlsHide(hideDelay);
  }

  function clearLightboxCloseSuppression() {
    window.clearTimeout(state.mediaLightboxSuppressCloseTimer);
    state.mediaLightboxSuppressCloseTimer = 0;
    state.mediaLightboxSuppressCloseClick = false;
  }

  function resetLightboxVideoControls() {
    clearLightboxVideoControlsTimer();
    clearLightboxCloseSuppression();
    state.mediaLightboxControlsRevealLocked = false;
    state.mediaLightboxControlsHotzoneSuppressed = false;
    state.mediaLightboxControlsLeaveDirection = "";
    state.mediaLightboxControlsPointerY = null;
    setLightboxVideoControlsVisible(true);
  }

  function pointerY(event) {
    return event.clientY ?? event.touches?.[0]?.clientY ?? event.changedTouches?.[0]?.clientY ?? 0;
  }

  function pointerX(event) {
    return event.clientX ?? event.touches?.[0]?.clientX ?? event.changedTouches?.[0]?.clientX ?? 0;
  }

  function isPointerNearLightboxControls(event) {
    if (!isLightboxVideoPlaying() || !el.mediaLightboxContent) return false;
    const rect = el.mediaLightboxContent.getBoundingClientRect();
    const controlsHeight = el.mediaLightboxControls?.offsetHeight || 48;
    return pointerY(event) >= rect.bottom - controlsHeight - VIDEO_CONTROLS_REVEAL_PADDING;
  }

  function isTouchLikeLightboxPointer(event) {
    if (event.pointerType) return event.pointerType === "touch" || event.pointerType === "pen";
    if (event.type?.startsWith("touch")) return true;
    return Boolean(window.matchMedia?.("(hover: none), (pointer: coarse)").matches);
  }

  function releaseLightboxProgressPointer(event) {
    const pointerId = event?.pointerId ?? state.mediaLightboxProgressPointerId;
    if (pointerId != null) {
      for (const target of [event?.currentTarget, el.mediaLightboxProgressTrack, el.mediaLightboxProgress]) {
        try {
          target?.releasePointerCapture?.(pointerId);
        } catch (_error) {
          // 指针捕获可能已被浏览器释放。
        }
      }
    }
    state.mediaLightboxProgressPointerId = null;
  }

  function setLightboxProgressFromPointer(event) {
    const rect = (el.mediaLightboxProgressTrack || el.mediaLightboxProgress).getBoundingClientRect();
    if (!rect.width) return;
    const ratio = Math.max(0, Math.min(1, (pointerX(event) - rect.left) / rect.width));
    const max = Number(el.mediaLightboxProgress.max || 1000);
    el.mediaLightboxProgress.value = String(Math.round(ratio * max));
    setLightboxVideoProgress(ratio);
    el.mediaLightboxCurrentTime.textContent = videoTimeLabel(lightboxVideoDuration() * ratio);
  }

  function beginLightboxProgressSeek(event) {
    event.stopPropagation();
    if (event.type?.startsWith("touch") && state.mediaLightboxProgressPointerId != null) {
      if (event.cancelable) event.preventDefault();
      return;
    }
    if (isTouchLikeLightboxPointer(event)) event.preventDefault();
    if (el.mediaLightboxProgress.disabled) return;
    clearLightboxVideoControlsTimer();
    if (event.pointerId != null) {
      state.mediaLightboxProgressPointerId = event.pointerId;
      try {
        event.currentTarget?.setPointerCapture?.(event.pointerId);
      } catch (_error) {
        state.mediaLightboxProgressPointerId = null;
      }
    }
    state.mediaLightboxProgressDragging = true;
    showLightboxVideoControls();
    setLightboxProgressFromPointer(event);
  }

  function handleLightboxProgressInput() {
    showLightboxVideoControls();
    state.mediaLightboxProgressDragging = true;
    const duration = lightboxVideoDuration();
    const ratio = Number(el.mediaLightboxProgress.value || 0) / Number(el.mediaLightboxProgress.max || 1000);
    setLightboxVideoProgress(ratio);
    el.mediaLightboxCurrentTime.textContent = videoTimeLabel(duration * ratio);
  }

  function commitLightboxProgressSeek(event) {
    if (event?.type?.startsWith("touch") && state.mediaLightboxProgressPointerId != null) {
      event.stopPropagation();
      if (event.cancelable) event.preventDefault();
    }
    if (
      event?.pointerId != null &&
      state.mediaLightboxProgressPointerId != null &&
      event.pointerId !== state.mediaLightboxProgressPointerId
    ) {
      return;
    }
    releaseLightboxProgressPointer(event);
    if (!state.mediaLightboxProgressDragging) return;
    state.mediaLightboxProgressDragging = false;
    const duration = lightboxVideoDuration();
    if (duration) {
      const ratio = Number(el.mediaLightboxProgress.value || 0) / Number(el.mediaLightboxProgress.max || 1000);
      el.mediaLightboxVideo.currentTime = duration * Math.max(0, Math.min(1, ratio));
    }
    syncLightboxVideoProgress({ force: true });
    clearLightboxCloseSuppression();
    scheduleLightboxVideoControlsHide(
      isTouchLikeLightboxPointer(event) ? VIDEO_CONTROLS_TOUCH_SEEK_HIDE_DELAY_MS : VIDEO_CONTROLS_REVEAL_HIDE_DELAY_MS
    );
  }

  function handleLightboxProgressTouchMove(event) {
    if (!state.mediaLightboxProgressDragging) return;
    if (event.type?.startsWith("touch") && state.mediaLightboxProgressPointerId != null) {
      event.stopPropagation();
      if (event.cancelable) event.preventDefault();
    }
    if (
      event.pointerId != null &&
      state.mediaLightboxProgressPointerId != null &&
      event.pointerId !== state.mediaLightboxProgressPointerId
    ) {
      return;
    }
    if (event.cancelable) event.preventDefault();
    event.stopPropagation();
    clearLightboxVideoControlsTimer();
    setLightboxProgressFromPointer(event);
  }

  function toggleLightboxVideoMuted(event) {
    event.stopPropagation();
    event.preventDefault();
    setLightboxVideoMuted(!el.mediaLightboxVideo.muted, { persist: true });
    showLightboxVideoControls({ autoHide: true });
  }

  function stopMediaControlClose(event) {
    event.stopPropagation();
    if (isLightboxVideoPlaying()) {
      showLightboxVideoControls({
        autoHide: true,
        hideDelay: isTouchLikeLightboxPointer(event)
          ? VIDEO_CONTROLS_TOUCH_REVEAL_HIDE_DELAY_MS
          : VIDEO_CONTROLS_REVEAL_HIDE_DELAY_MS,
      });
    }
  }

  function suppressLightboxCloseClickOnce() {
    clearLightboxCloseSuppression();
    state.mediaLightboxSuppressCloseClick = true;
    state.mediaLightboxSuppressCloseTimer = window.setTimeout(clearLightboxCloseSuppression, 420);
  }

  function consumeSuppressedCloseClick(event) {
    if (!state.mediaLightboxSuppressCloseClick) return false;
    clearLightboxCloseSuppression();
    event.preventDefault();
    event.stopPropagation();
    return true;
  }

  function handleLightboxContentPointerMove(event) {
    if (isTouchLikeLightboxPointer(event)) return;
    const currentY = pointerY(event);
    const previousY = state.mediaLightboxControlsPointerY;
    const movingUp = previousY != null && currentY < previousY;
    state.mediaLightboxControlsPointerY = currentY;
    const nearControls = isPointerNearLightboxControls(event);
    if (!nearControls) {
      state.mediaLightboxControlsRevealLocked = false;
      state.mediaLightboxControlsHotzoneSuppressed = false;
      state.mediaLightboxControlsLeaveDirection = "";
      if (isLightboxVideoPlaying()) {
        scheduleLightboxVideoControlsHide(VIDEO_CONTROLS_LEAVE_HIDE_DELAY_MS);
      }
      return;
    }
    if (state.mediaLightboxControlsHotzoneSuppressed) {
      const canRevealFromReturn =
        (state.mediaLightboxControlsLeaveDirection === "down" && movingUp) ||
        (state.mediaLightboxControlsLeaveDirection === "up" && !movingUp);
      if (!canRevealFromReturn) return;
      state.mediaLightboxControlsHotzoneSuppressed = false;
      state.mediaLightboxControlsLeaveDirection = "";
    }
    if (!state.mediaLightboxControlsRevealLocked) {
      showLightboxVideoControls();
    }
  }

  function handleLightboxCloseSurfacePointerDown(event) {
    if (!isPointerNearLightboxControls(event)) return;
    const touchLike = isTouchLikeLightboxPointer(event);
    if (state.mediaLightboxControlsRevealLocked && !touchLike) return;
    suppressLightboxCloseClickOnce();
    showLightboxVideoControls({
      autoHide: touchLike,
      hideDelay: VIDEO_CONTROLS_TOUCH_REVEAL_HIDE_DELAY_MS,
    });
    event.preventDefault();
  }

  function handleLightboxControlsPointerEnter(event) {
    if (isTouchLikeLightboxPointer(event)) return;
    state.mediaLightboxControlsPointerY = pointerY(event);
    state.mediaLightboxControlsHotzoneSuppressed = false;
    state.mediaLightboxControlsLeaveDirection = "";
    showLightboxVideoControls();
  }

  function handleLightboxControlsPointerLeave(event) {
    if (isTouchLikeLightboxPointer(event)) return;
    const currentY = pointerY(event);
    const rect = el.mediaLightboxControls.getBoundingClientRect();
    state.mediaLightboxControlsPointerY = currentY;
    state.mediaLightboxControlsHotzoneSuppressed = true;
    state.mediaLightboxControlsLeaveDirection = currentY < rect.top + rect.height / 2 ? "up" : "down";
    scheduleLightboxVideoControlsHide(VIDEO_CONTROLS_LEAVE_HIDE_DELAY_MS);
  }

  function syncLightboxVideoControls() {
    if (el.mediaLightboxVideo.hidden || el.mediaLightbox.classList.contains("is-closing")) return;
    const isPlaying = isLightboxVideoPlaying();
    el.mediaLightbox.classList.toggle("video-is-playing", isPlaying);
    el.mediaLightboxPlayButton.hidden = false;
    if (isPlaying) {
      showLightboxVideoControls();
      scheduleLightboxVideoControlsHide(VIDEO_CONTROLS_HIDE_DELAY_MS, { lockReveal: true });
    } else {
      showLightboxVideoControls();
    }
    el.mediaLightboxPlayButton.setAttribute("aria-label", isPlaying ? "暂停视频" : "播放视频");
    el.mediaLightboxPlayButton.setAttribute("aria-pressed", isPlaying ? "true" : "false");
  }

  function toggleLightboxVideoPlayback(event) {
    event.stopPropagation();
    event.preventDefault();
    if (el.mediaLightboxVideo.hidden) return;
    if (isLightboxVideoPlaying()) {
      el.mediaLightboxVideo.pause();
      syncLightboxVideoControls();
      return;
    }
    const playResult = el.mediaLightboxVideo.play();
    if (playResult?.catch) playResult.catch(syncLightboxVideoControls);
    syncLightboxVideoControls();
  }

  return {
    applyStoredVideoMuted,
    beginLightboxProgressSeek,
    clearLightboxCloseSuppression,
    clearLightboxVideoControlsTimer,
    commitLightboxProgressSeek,
    consumeSuppressedCloseClick,
    handleLightboxCloseSurfacePointerDown,
    handleLightboxContentPointerMove,
    handleLightboxControlsPointerEnter,
    handleLightboxControlsPointerLeave,
    handleLightboxProgressInput,
    handleLightboxProgressTouchMove,
    resetLightboxVideoControls,
    resetLightboxVideoProgress,
    stopMediaControlClose,
    syncLightboxVideoControls,
    syncLightboxVideoMuteButton,
    syncLightboxVideoProgress,
    toggleLightboxVideoMuted,
    toggleLightboxVideoPlayback,
  };
}
