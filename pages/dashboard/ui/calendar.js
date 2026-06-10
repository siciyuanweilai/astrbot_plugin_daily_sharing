import {
  emptyNode,
  formatDateOnly,
  replaceChildren,
  text,
} from "./format.js?v=20260609-format";

const stackedQuery = "(max-width: 1100px)";
const desktopColumns = 2;
const stackedItems = 24;
const stackedDayOverheadUnits = 3;
const fallbackHeight = 320;
const itemHeight = 24;
const dayOverheadHeight = 44;
const listGap = 9;
const heightSafety = 6;

export function createCalendarUi({
  state,
  elements: el,
  carouselIntervalMs = 5200,
} = {}) {
  function isCalendarStackedLayout() {
    return window.matchMedia?.(stackedQuery).matches || window.innerWidth <= 1100;
  }

  function calendarPageHeightBudget() {
    const listHeight = el.calendarList?.clientHeight || fallbackHeight;
    return Math.max(0, listHeight - heightSafety);
  }

  function calendarSignature(calendar) {
    return calendar
      .map((day) => {
        const items = Array.isArray(day.items) ? day.items : [];
        return [
          text(day.date),
          items.length,
          ...items.map((item) => `${text(item.time)}|${text(item.name)}|${text(item.id)}`),
        ].join("~");
      })
      .join(";");
  }

  function paginateCalendarByItemCount(calendar) {
    const pages = [];
    let page = [];
    let used = 0;

    const pushPage = () => {
      if (!page.length) return;
      pages.push(page);
      page = [];
      used = 0;
    };

    for (const day of calendar) {
      const items = Array.isArray(day.items) ? day.items : [];
      if (!items.length) {
        if (used && used + stackedDayOverheadUnits > stackedItems) pushPage();
        page.push({ date: day.date, items: [], continued: false });
        used += stackedDayOverheadUnits;
        continue;
      }

      for (let index = 0; index < items.length;) {
        if (used && used + stackedDayOverheadUnits + 1 > stackedItems) pushPage();
        const availableItems = Math.max(1, stackedItems - used - stackedDayOverheadUnits);
        const nextItems = items.slice(index, index + availableItems);
        page.push({
          date: day.date,
          items: nextItems,
          continued: index > 0,
        });
        used += stackedDayOverheadUnits + nextItems.length;
        index += nextItems.length;
        if (index < items.length) pushPage();
      }
    }

    pushPage();
    return pages;
  }

  function calendarPageColumnCount() {
    return isCalendarStackedLayout() ? 1 : desktopColumns;
  }

  function calendarPageEntryHeight(itemCount) {
    const itemRows = Math.ceil(itemCount / calendarPageColumnCount());
    return dayOverheadHeight + itemRows * itemHeight;
  }

  function paginateCalendarByHeight(calendar) {
    const heightBudget = calendarPageHeightBudget();
    const pages = [];
    let page = [];
    let usedHeight = 0;

    const pushPage = () => {
      if (!page.length) return;
      pages.push(page);
      page = [];
      usedHeight = 0;
    };

    const addEntry = (entry, itemCount) => {
      const gap = page.length ? listGap : 0;
      page.push(entry);
      usedHeight += gap + calendarPageEntryHeight(itemCount);
    };

    for (const day of calendar) {
      const items = Array.isArray(day.items) ? day.items : [];
      if (!items.length) {
        const nextHeight = (page.length ? listGap : 0) + calendarPageEntryHeight(0);
        if (page.length && usedHeight + nextHeight > heightBudget) pushPage();
        addEntry({ date: day.date, items: [], continued: false }, 0);
        continue;
      }

      for (let index = 0; index < items.length;) {
        const gap = page.length ? listGap : 0;
        let availableHeight = heightBudget - usedHeight - gap - dayOverheadHeight;
        if (page.length && availableHeight < itemHeight) {
          pushPage();
          availableHeight = heightBudget - dayOverheadHeight;
        }

        const availableRows = Math.max(1, Math.floor(availableHeight / itemHeight));
        const availableItems = availableRows * calendarPageColumnCount();
        const nextItems = items.slice(index, index + availableItems);
        addEntry({
          date: day.date,
          items: nextItems,
          continued: index > 0,
        }, nextItems.length);
        index += nextItems.length;
        if (index < items.length) pushPage();
      }
    }

    pushPage();
    return pages;
  }

  function createCalendarMeasureList() {
    if (!el.calendarPanel || !el.calendarList) return null;
    const width = el.calendarList.clientWidth;
    const heightBudget = calendarPageHeightBudget();
    if (width <= 0 || heightBudget <= 0) return null;
    const measureList = document.createElement("div");
    measureList.className = "calendar-list calendar-measure-list";
    measureList.setAttribute("aria-hidden", "true");
    measureList.style.width = `${width}px`;
    el.calendarPanel.append(measureList);
    return measureList;
  }

  function measureCalendarPage(measureList, page) {
    replaceChildren(measureList, page.map(calendarDayNode));
    const rectHeight = measureList.getBoundingClientRect().height;
    return Math.ceil(Math.max(measureList.scrollHeight, rectHeight));
  }

  function estimateCalendarPageHeight(page) {
    return page.reduce((height, entry, index) => {
      const itemCount = Array.isArray(entry.items) ? entry.items.length : 0;
      const gap = index ? listGap : 0;
      return height + gap + calendarPageEntryHeight(itemCount);
    }, 0);
  }

  function calendarStableListHeight(pages) {
    if (!isCalendarStackedLayout() || !el.calendarPanel || !el.calendarList || !pages.length) return 0;
    const width = el.calendarList.clientWidth;
    if (width <= 0) {
      return Math.max(...pages.map(estimateCalendarPageHeight));
    }

    const measureList = document.createElement("div");
    measureList.className = "calendar-list calendar-measure-list";
    measureList.setAttribute("aria-hidden", "true");
    measureList.style.width = `${width}px`;
    el.calendarPanel.append(measureList);
    try {
      return Math.max(...pages.map((page) => measureCalendarPage(measureList, page)));
    } finally {
      measureList.remove();
    }
  }

  function applyCalendarStableListHeight(pages) {
    if (!el.calendarList) return;
    const height = calendarStableListHeight(pages);
    if (height <= 0) {
      state.calendarStableListHeight = 0;
      el.calendarList.style.removeProperty("min-height");
      return;
    }
    state.calendarStableListHeight = height;
    el.calendarList.style.minHeight = `${height}px`;
  }

  function calendarPageFits(measureList, page, heightBudget) {
    return measureCalendarPage(measureList, page) <= heightBudget;
  }

  function paginateCalendarByMeasuredHeight(calendar) {
    const heightBudget = calendarPageHeightBudget();
    const measureList = createCalendarMeasureList();
    if (!measureList) return paginateCalendarByHeight(calendar);

    const pages = [];
    let page = [];

    const pushPage = () => {
      if (!page.length) return;
      pages.push(page);
      page = [];
    };

    try {
      for (const day of calendar) {
        const items = Array.isArray(day.items) ? day.items : [];
        if (!items.length) {
          const entry = { date: day.date, items: [], continued: false };
          if (page.length && !calendarPageFits(measureList, [...page, entry], heightBudget)) pushPage();
          page.push(entry);
          continue;
        }

        for (let index = 0; index < items.length;) {
          let entry = { date: day.date, items: [], continued: index > 0 };

          while (index < items.length) {
            const candidateEntry = { ...entry, items: [...entry.items, items[index]] };
            const candidatePage = [...page, candidateEntry];
            const isMinimumPage = !page.length && !entry.items.length;
            if (calendarPageFits(measureList, candidatePage, heightBudget) || isMinimumPage) {
              entry = candidateEntry;
              index += 1;
              continue;
            }
            break;
          }

          if (!entry.items.length) {
            pushPage();
            continue;
          }

          page.push(entry);
          if (index < items.length) pushPage();
        }
      }
    } finally {
      measureList.remove();
    }

    pushPage();
    return pages;
  }

  function paginateCalendar(calendar) {
    return isCalendarStackedLayout()
      ? paginateCalendarByItemCount(calendar)
      : paginateCalendarByMeasuredHeight(calendar);
  }

  function syncCalendarPanelHeight() {
    if (!el.primaryColumn || !el.calendarPanel) return;
    if (isCalendarStackedLayout()) {
      el.calendarPanel.style.removeProperty("height");
      return;
    }

    const height = el.primaryColumn.getBoundingClientRect().height;
    if (height > 0) {
      el.calendarPanel.style.height = `${height}px`;
    }
  }

  function scheduleCalendarPanelLayout({ rerender = false } = {}) {
    state.calendarLayoutNeedsRender = state.calendarLayoutNeedsRender || rerender;
    window.cancelAnimationFrame(state.calendarLayoutFrame);
    state.calendarLayoutFrame = window.requestAnimationFrame(() => {
      state.calendarLayoutFrame = 0;
      const shouldRender = state.calendarLayoutNeedsRender;
      state.calendarLayoutNeedsRender = false;
      syncCalendarPanelHeight();
      if (shouldRender) renderCalendar({ scheduleLayout: false });
    });
  }

  function handleCalendarLayoutResize() {
    scheduleCalendarPanelLayout({ rerender: true });
  }

  function initCalendarPanelLayout() {
    scheduleCalendarPanelLayout({ rerender: true });
    window.addEventListener("resize", handleCalendarLayoutResize);
    if (!("ResizeObserver" in window) || !el.primaryColumn) return;
    state.calendarResizeObserver = new ResizeObserver(handleCalendarLayoutResize);
    state.calendarResizeObserver.observe(el.primaryColumn);
  }

  function calendarItemNode(item = {}) {
    const row = document.createElement("div");
    row.className = "calendar-item";
    const time = document.createElement("span");
    time.textContent = item.time || "--:--";
    const name = document.createElement("span");
    name.textContent = item.name || "任务";
    row.append(time, name);
    return row;
  }

  function calendarDayNode(entry) {
    const node = document.createElement("article");
    node.className = "calendar-day";
    const date = document.createElement("strong");
    date.textContent = formatDateOnly(entry.date);
    if (entry.continued) {
      const continued = document.createElement("span");
      continued.className = "calendar-continued";
      continued.textContent = "续";
      date.appendChild(continued);
    }
    node.append(date);

    if (!entry.items.length) {
      node.append(emptyNode("暂无任务"));
      return node;
    }
    const list = document.createElement("div");
    list.className = "calendar-items";
    for (const item of entry.items) {
      list.append(calendarItemNode(item));
    }
    node.append(list);
    return node;
  }

  function stopCalendarCarouselTimer() {
    window.clearTimeout(state.calendarCarouselTimer);
    state.calendarCarouselTimer = 0;
  }

  function scheduleCalendarCarousel() {
    stopCalendarCarouselTimer();
    if (
      state.activeView !== "dashboard" ||
      el.dashboardView?.hidden ||
      el.calendarPanel?.matches(":hover") ||
      el.calendarPanel?.contains(document.activeElement)
    ) {
      return;
    }
    if (state.calendarPageCount <= 1) return;
    state.calendarCarouselTimer = window.setTimeout(() => {
      setCalendarPage(state.calendarPageIndex + 1);
    }, carouselIntervalMs);
  }

  function setCalendarPage(index) {
    if (!state.calendarPageCount) return;
    state.calendarPageIndex = ((index % state.calendarPageCount) + state.calendarPageCount) % state.calendarPageCount;
    renderCalendar({ scheduleLayout: false });
  }

  function renderCalendar({ scheduleLayout = true } = {}) {
    const calendar = state.status?.scheduler?.calendar || [];
    if (!calendar.length) {
      stopCalendarCarouselTimer();
      state.calendarSignature = "";
      state.calendarPageIndex = 0;
      state.calendarPageCount = 0;
      applyCalendarStableListHeight([]);
      el.calendarList?.classList.remove("is-single-day");
      replaceChildren(el.calendarList, [emptyNode()]);
      if (scheduleLayout) scheduleCalendarPanelLayout({ rerender: true });
      return;
    }

    const signature = calendarSignature(calendar);
    if (signature !== state.calendarSignature) {
      state.calendarSignature = signature;
      state.calendarPageIndex = 0;
    }

    const pages = paginateCalendar(calendar);
    state.calendarPageCount = pages.length;
    state.calendarPageIndex = Math.max(0, Math.min(state.calendarPageIndex, pages.length - 1));
    const currentPage = pages[state.calendarPageIndex] || [];
    applyCalendarStableListHeight(pages);
    el.calendarList?.classList.toggle("is-single-day", currentPage.length === 1);
    replaceChildren(el.calendarList, currentPage.map(calendarDayNode));
    scheduleCalendarCarousel();
    if (scheduleLayout) scheduleCalendarPanelLayout({ rerender: true });
  }

  return {
    initCalendarPanelLayout,
    renderCalendar,
    scheduleCalendarCarousel,
    scheduleCalendarPanelLayout,
    stopCalendarCarouselTimer,
  };
}
