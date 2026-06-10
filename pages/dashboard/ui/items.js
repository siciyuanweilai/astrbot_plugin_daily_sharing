import {
  clampContent,
  formatMediaTime,
  fullContent,
  itemTargetLabel,
  text,
  typeLabel,
} from "./format.js?v=20260609-format";

export function mediaKind(item) {
  const rawType = text(item.media_type).toLowerCase();
  if (rawType.includes("image")) return "image";
  if (rawType.includes("video")) return "video";

  const ref = text(item.preview_url || item.media_url || item.media_path).toLowerCase();
  if (ref.startsWith("data:image/") || /\.(png|jpe?g|webp|gif|bmp|avif)(?:[?#].*)?$/.test(ref)) {
    return "image";
  }
  if (ref.startsWith("data:video/") || /\.(mp4|webm|mov|m4v|avi|mkv)(?:[?#].*)?$/.test(ref)) {
    return "video";
  }
  return "";
}

export function mediaTitle(item) {
  return `${itemTargetLabel(item)} · ${typeLabel(item.type)}`;
}

export function mediaPreviewUrl(item) {
  return text(item.preview_url || item.media_url).trim();
}

export function isVisualMediaKind(kind) {
  return kind === "image" || kind === "video";
}

export function mediaCaption(item) {
  const title = mediaTitle(item);
  const time = formatMediaTime(item.timestamp);
  return `${title} · ${time}`;
}

function mediaPlaceholderText(kind) {
  if (kind === "image") return "图片";
  if (kind === "video") return "视频";
  return "文案";
}

function mediaPreviewAriaLabel(item, kind) {
  return `${kind === "video" ? "查看视频" : "查看图片"}：${mediaCaption(item)}`;
}

function contentPreviewButton(content, title, openTextLightbox, max = 180) {
  const body = fullContent(content);
  const node = document.createElement("button");
  node.className = "item-meta content-preview-button";
  node.type = "button";
  node.textContent = clampContent(body, max) || "查看文案";
  node.disabled = !body;
  node.addEventListener("click", () => openTextLightbox?.(title, body));
  return node;
}

function createMediaImagePreview(preview, item, previewUrl, openImagePreviewLightbox) {
  const img = document.createElement("img");
  img.src = previewUrl;
  img.alt = clampContent(item.content, 40) || "分享图片";
  preview.append(img);
  preview.addEventListener("click", () => openImagePreviewLightbox?.(item, previewUrl, img));
}

function createMediaVideoPreview(preview, item, previewUrl, kind, openMediaLightbox) {
  const video = document.createElement("video");
  video.src = previewUrl;
  video.muted = true;
  video.controls = false;
  video.playsInline = true;
  video.preload = "metadata";
  video.tabIndex = -1;
  video.removeAttribute("controls");
  video.setAttribute("aria-hidden", "true");
  video.setAttribute("playsinline", "");
  video.setAttribute("webkit-playsinline", "");
  const badge = document.createElement("span");
  badge.className = "media-video-badge";
  badge.textContent = "视频";
  preview.append(video, badge);
  preview.addEventListener("click", () => openMediaLightbox?.(previewUrl, mediaCaption(item), kind));
}

function createMediaPreview(item, kind, previewUrl, handlers) {
  const canOpenPreview = isVisualMediaKind(kind) && previewUrl;
  const preview = document.createElement(canOpenPreview ? "button" : "div");
  preview.className = "media-preview";
  if (kind === "video") preview.classList.add("video");

  if (canOpenPreview && kind === "image") {
    createMediaImagePreview(preview, item, previewUrl, handlers.openImagePreviewLightbox);
  } else if (canOpenPreview && kind === "video") {
    createMediaVideoPreview(preview, item, previewUrl, kind, handlers.openMediaLightbox);
  } else {
    const placeholder = document.createElement("span");
    placeholder.textContent = mediaPlaceholderText(kind);
    preview.append(placeholder);
  }

  if (canOpenPreview) {
    preview.type = "button";
    preview.classList.add("media-preview-button");
    preview.setAttribute("aria-label", mediaPreviewAriaLabel(item, kind));
  }

  return preview;
}

function createMediaMeta(item, openTextLightbox) {
  const meta = document.createElement("div");
  meta.className = "media-meta";
  const titleText = mediaTitle(item);
  const title = document.createElement("strong");
  title.textContent = titleText;
  const path = document.createElement("span");
  path.className = "item-meta";
  path.textContent = formatMediaTime(item.timestamp);
  const body = contentPreviewButton(item.content, titleText, openTextLightbox, 70);
  meta.append(title, path, body);
  return meta;
}

export function createMediaItem(item, handlers = {}) {
  const node = document.createElement("article");
  node.className = "media-item";
  const kind = mediaKind(item);
  const previewUrl = mediaPreviewUrl(item);
  const hasVisualPreview = Boolean(previewUrl && isVisualMediaKind(kind));
  node.classList.toggle("has-media-underlay", hasVisualPreview);
  node.classList.toggle("text-only", !hasVisualPreview);
  node.append(
    createMediaPreview(item, kind, previewUrl, handlers),
    createMediaMeta(item, handlers.openTextLightbox),
  );
  return node;
}
