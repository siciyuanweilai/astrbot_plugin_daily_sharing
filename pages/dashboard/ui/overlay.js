import { fullContent, text } from "./format.js?v=20260609-format";
import { LIGHTBOX_ANIMATION_MS } from "./constants.js?v=20260609-media-flat";

export function createOverlayController({ state, elements: el } = {}) {
  function overlayScrollTarget(target) {
    if (!(target instanceof Element)) return null;
    const textBody = target.closest(".text-lightbox-body");
    if (textBody && !el.textLightbox.hidden) return textBody;
    const mediaBox = target.closest(".media-lightbox");
    if (mediaBox && !el.mediaLightbox.hidden) return mediaBox;
    return null;
  }

  function canScrollTarget(target, deltaY) {
    if (!target) return false;
    const maxScroll = target.scrollHeight - target.clientHeight;
    if (maxScroll <= 1) return false;
    if (deltaY < 0) return target.scrollTop > 0;
    if (deltaY > 0) return target.scrollTop < maxScroll;
    return true;
  }

  function keepOverlayScrollPosition() {
    if (!state.overlayLockCount) return;
    if (Math.abs(window.scrollY - state.overlayScrollY) > 1) {
      window.scrollTo(0, state.overlayScrollY);
    }
  }

  function handleOverlayWheel(event) {
    if (!state.overlayLockCount) return;
    const target = overlayScrollTarget(event.target);
    if (canScrollTarget(target, event.deltaY)) return;
    event.preventDefault();
  }

  function handleOverlayTouchStart(event) {
    state.overlayTouchY = event.touches?.[0]?.clientY || 0;
  }

  function handleOverlayTouchMove(event) {
    if (!state.overlayLockCount) return;
    const currentY = event.touches?.[0]?.clientY || 0;
    const deltaY = state.overlayTouchY - currentY;
    state.overlayTouchY = currentY;
    const target = overlayScrollTarget(event.target);
    if (canScrollTarget(target, deltaY)) return;
    event.preventDefault();
  }

  function lockPageScroll() {
    if (state.overlayLockCount === 0) {
      state.overlayScrollY = window.scrollY || document.documentElement.scrollTop || 0;
      window.addEventListener("wheel", handleOverlayWheel, { passive: false });
      window.addEventListener("touchstart", handleOverlayTouchStart, { passive: true });
      window.addEventListener("touchmove", handleOverlayTouchMove, { passive: false });
      window.addEventListener("scroll", keepOverlayScrollPosition, { passive: true });
    }
    state.overlayLockCount += 1;
  }

  function unlockPageScroll() {
    if (state.overlayLockCount <= 0) return;
    state.overlayLockCount -= 1;
    if (state.overlayLockCount > 0) return;
    window.removeEventListener("wheel", handleOverlayWheel);
    window.removeEventListener("touchstart", handleOverlayTouchStart);
    window.removeEventListener("touchmove", handleOverlayTouchMove);
    window.removeEventListener("scroll", keepOverlayScrollPosition);
    window.scrollTo(0, state.overlayScrollY);
  }

  function openTextLightbox(title, content) {
    const body = fullContent(content);
    if (!body) return;
    const wasHidden = el.textLightbox.hidden;
    if (state.textLightboxCloseTimer) {
      window.clearTimeout(state.textLightboxCloseTimer);
      state.textLightboxCloseTimer = 0;
    }
    el.textLightbox.classList.remove("is-closing");
    el.textLightboxTitle.textContent = title || "全文";
    el.textLightboxBody.textContent = body;
    document.documentElement.classList.add("text-lightbox-open");
    document.body.classList.add("text-lightbox-open");
    el.textLightbox.hidden = false;
    if (wasHidden) lockPageScroll();
  }

  function closeTextLightbox() {
    if (el.textLightbox.hidden || el.textLightbox.classList.contains("is-closing")) return;
    el.textLightbox.classList.add("is-closing");
    state.textLightboxCloseTimer = window.setTimeout(() => {
      state.textLightboxCloseTimer = 0;
      el.textLightbox.hidden = true;
      el.textLightbox.classList.remove("is-closing");
      document.documentElement.classList.remove("text-lightbox-open");
      document.body.classList.remove("text-lightbox-open");
      el.textLightboxTitle.textContent = "全文";
      el.textLightboxBody.textContent = "";
      unlockPageScroll();
    }, LIGHTBOX_ANIMATION_MS);
  }

  function hasTextLightboxSelection() {
    const selection = window.getSelection?.();
    if (!selection || selection.isCollapsed || !text(selection.toString()).trim()) return false;
    for (let index = 0; index < selection.rangeCount; index += 1) {
      const range = selection.getRangeAt(index);
      if (range.intersectsNode?.(el.textLightboxBody)) return true;
      const node = range.commonAncestorContainer;
      const element = node instanceof Element ? node : node?.parentElement;
      if (element && el.textLightboxBody.contains(element)) return true;
    }
    return false;
  }

  function handleTextLightboxClick(event) {
    if (event.button !== 0 || hasTextLightboxSelection()) return;
    closeTextLightbox();
  }

  return {
    closeTextLightbox,
    handleTextLightboxClick,
    lockPageScroll,
    openTextLightbox,
    unlockPageScroll,
  };
}
