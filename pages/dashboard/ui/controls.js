import { text } from "./format.js?v=20260609-format";

const menuGap = 6;
const viewportPadding = 12;
const menuMaxHeight = 268;
const menuMinHeight = 96;

export function createSweetControls({ selects = [], combos = [] } = {}) {
  const sweetSelectControllers = new Map();
  const sweetComboControllers = new Map();

  function sweetMenuPlacement(anchor, menu) {
    const rect = anchor.getBoundingClientRect();
    const spaceBelow = window.innerHeight - rect.bottom - viewportPadding;
    const spaceAbove = rect.top - viewportPadding;
    const menuHeight = Math.min(menu.scrollHeight || menuMaxHeight, menuMaxHeight);
    const dropUp = spaceBelow < menuHeight && spaceAbove > spaceBelow;
    const available = Math.max(
      menuMinHeight,
      Math.min(menuMaxHeight, (dropUp ? spaceAbove : spaceBelow) - menuGap),
    );
    return { dropUp, available };
  }

  function applySweetMenuPlacement(controller, anchor) {
    if (!controller || controller.menu.hidden) return;
    const { dropUp, available } = sweetMenuPlacement(anchor, controller.menu);
    controller.wrapper.classList.toggle("is-drop-up", dropUp);
    controller.wrapper.classList.toggle("is-drop-down", !dropUp);
    controller.wrapper.style.setProperty("--sweet-select-menu-max-height", `${available}px`);
  }

  function clearSweetMenuPlacement(controller) {
    controller.wrapper.classList.remove("is-drop-up", "is-drop-down");
    controller.wrapper.style.removeProperty("--sweet-select-menu-max-height");
  }

  function closeSweetControlSet(controllers, closeControl, except = null) {
    for (const control of controllers.keys()) {
      if (control !== except) closeControl(control);
    }
  }

  function selectControlLabel(select) {
    const explicit = text(select.getAttribute("aria-label")).trim();
    if (explicit) return explicit;
    const label = select.closest("label");
    const caption = label
      ? [...label.children].find((child) => child.tagName === "SPAN")?.textContent
      : "";
    return text(caption).trim() || "下拉选择";
  }

  function selectOptionText(option) {
    return text(option?.textContent || option?.label || option?.value).trim() || "请选择";
  }

  function currentSelectOption(select) {
    return select.options[select.selectedIndex] || select.options[0] || null;
  }

  function selectableOptionIndexes(select) {
    return [...select.options]
      .map((option, index) => (option.disabled ? -1 : index))
      .filter((index) => index >= 0);
  }

  function sweetSelectOptionSignature(select) {
    return [...select.options]
      .map((option) => [
        option.value,
        selectOptionText(option),
        option.disabled ? "1" : "0",
      ].join("\u001f"))
      .join("\u001e");
  }

  function buildSweetSelectOptions(select, controller, selected) {
    return [...select.options].map((option, index) => {
      const item = document.createElement("button");
      item.type = "button";
      item.id = `${controller.id}-option-${index}`;
      item.className = "sweet-select-option";
      item.dataset.index = String(index);
      item.role = "option";
      item.addEventListener("click", () => commitSweetSelect(select, index));
      return syncSweetSelectOption(item, option, option === selected);
    });
  }

  function syncSweetSelectOption(item, option, isSelected) {
    item.disabled = option.disabled;
    item.textContent = selectOptionText(option);
    item.setAttribute("aria-selected", isSelected ? "true" : "false");
    item.classList.toggle("is-selected", isSelected);
    return item;
  }

  function setSweetSelectActive(select, index) {
    const controller = sweetSelectControllers.get(select);
    if (!controller) return;
    const selectable = selectableOptionIndexes(select);
    const fallback = selectable.includes(select.selectedIndex) ? select.selectedIndex : selectable[0] ?? -1;
    controller.activeIndex = selectable.includes(index) ? index : fallback;
    for (const option of controller.menu.querySelectorAll(".sweet-select-option")) {
      const active = Number(option.dataset.index) === controller.activeIndex;
      option.classList.toggle("is-active", active);
      if (active) {
        controller.trigger.setAttribute("aria-activedescendant", option.id);
        if (!controller.menu.hidden) option.scrollIntoView({ block: "nearest" });
      }
    }
  }

  function syncSweetSelect(select) {
    const controller = sweetSelectControllers.get(select);
    if (!controller) return;
    const selected = currentSelectOption(select);
    const selectedText = selectOptionText(selected);
    const disabled = select.disabled || !select.options.length;
    const optionSignature = sweetSelectOptionSignature(select);

    if (optionSignature !== controller.optionSignature) {
      controller.menu.replaceChildren(...buildSweetSelectOptions(select, controller, selected));
      controller.optionSignature = optionSignature;
    } else {
      for (const item of controller.menu.querySelectorAll(".sweet-select-option")) {
        const option = select.options[Number(item.dataset.index)];
        if (option) syncSweetSelectOption(item, option, option === selected);
      }
    }

    controller.value.textContent = selectedText;
    controller.wrapper.classList.toggle("is-disabled", disabled);
    controller.trigger.disabled = disabled;
    controller.trigger.setAttribute("aria-disabled", disabled ? "true" : "false");
    controller.trigger.setAttribute("aria-label", `${selectControlLabel(select)}：${selectedText}`);
    setSweetSelectActive(select, selected ? select.selectedIndex : -1);
  }

  function closeSweetSelect(select) {
    const controller = sweetSelectControllers.get(select);
    if (!controller || !controller.wrapper.classList.contains("is-open")) return;
    controller.wrapper.classList.remove("is-open");
    clearSweetMenuPlacement(controller);
    controller.panel?.classList.remove("has-open-select");
    controller.overlayHost?.classList.remove("has-open-select");
    controller.menu.hidden = true;
    controller.trigger.setAttribute("aria-expanded", "false");
    controller.trigger.removeAttribute("aria-activedescendant");
  }

  function closeSweetSelects(except = null) {
    closeSweetControlSet(sweetSelectControllers, closeSweetSelect, except);
  }

  function updateSweetSelectPlacement(select) {
    const controller = sweetSelectControllers.get(select);
    if (controller) applySweetMenuPlacement(controller, controller.trigger);
  }

  function updateOpenSweetSelectPlacements() {
    for (const select of sweetSelectControllers.keys()) {
      updateSweetSelectPlacement(select);
    }
  }

  function openSweetSelect(select) {
    const controller = sweetSelectControllers.get(select);
    if (!controller || controller.trigger.disabled) return;
    syncSweetSelect(select);
    closeSweetCombos();
    closeSweetSelects(select);
    controller.wrapper.classList.add("is-open");
    controller.panel?.classList.add("has-open-select");
    controller.overlayHost?.classList.add("has-open-select");
    controller.menu.hidden = false;
    updateSweetSelectPlacement(select);
    controller.trigger.setAttribute("aria-expanded", "true");
    setSweetSelectActive(select, select.selectedIndex);
  }

  function moveSweetSelectActive(select, step) {
    const selectable = selectableOptionIndexes(select);
    if (!selectable.length) return;
    const controller = sweetSelectControllers.get(select);
    const current = controller?.activeIndex ?? select.selectedIndex;
    const currentPosition = Math.max(0, selectable.indexOf(current));
    const nextPosition = (currentPosition + step + selectable.length) % selectable.length;
    setSweetSelectActive(select, selectable[nextPosition]);
  }

  function commitSweetSelect(select, index) {
    const option = select.options[index];
    const controller = sweetSelectControllers.get(select);
    if (!option || option.disabled || !controller) return;
    const previous = select.value;
    select.selectedIndex = index;
    syncSweetSelect(select);
    closeSweetSelect(select);
    controller.trigger.focus({ preventScroll: true });
    if (select.value !== previous) {
      select.dispatchEvent(new Event("change", { bubbles: true }));
    }
  }

  function handleSweetSelectKeydown(select, event) {
    const controller = sweetSelectControllers.get(select);
    if (!controller) return;
    const open = controller.wrapper.classList.contains("is-open");
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (!open) openSweetSelect(select);
      moveSweetSelectActive(select, event.key === "ArrowDown" ? 1 : -1);
      return;
    }
    if (event.key === "Home" || event.key === "End") {
      event.preventDefault();
      if (!open) openSweetSelect(select);
      const selectable = selectableOptionIndexes(select);
      setSweetSelectActive(select, event.key === "Home" ? selectable[0] : selectable.at(-1));
      return;
    }
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      if (open) {
        commitSweetSelect(select, controller.activeIndex);
      } else {
        openSweetSelect(select);
      }
      return;
    }
    if (event.key === "Escape" && open) {
      event.preventDefault();
      closeSweetSelect(select);
    }
  }

  function initSweetSelect(select) {
    if (!select || sweetSelectControllers.has(select)) return;
    const id = `sweet-select-${select.id || sweetSelectControllers.size}`;
    const wrapper = document.createElement("div");
    const trigger = document.createElement("button");
    const value = document.createElement("span");
    const arrow = document.createElement("span");
    const menu = document.createElement("div");
    const panel = select.closest(".panel");
    const overlayHost = select.closest(".control-grid, .panel-head");

    wrapper.className = "sweet-select";
    if (select.classList.contains("compact-select")) wrapper.classList.add("is-compact");
    if (select.classList.contains("media-type-filter")) wrapper.classList.add("is-media-type");
    wrapper.dataset.selectFor = select.id || "";

    trigger.type = "button";
    trigger.className = "sweet-select-trigger";
    trigger.setAttribute("aria-haspopup", "listbox");
    trigger.setAttribute("aria-expanded", "false");
    trigger.setAttribute("aria-controls", `${id}-listbox`);

    value.className = "sweet-select-value";
    arrow.className = "sweet-select-arrow";
    arrow.setAttribute("aria-hidden", "true");
    menu.id = `${id}-listbox`;
    menu.className = "sweet-select-menu";
    menu.role = "listbox";
    menu.hidden = true;

    trigger.append(value, arrow);
    wrapper.append(trigger, menu);
    select.classList.add("native-select");
    select.tabIndex = -1;
    select.setAttribute("aria-hidden", "true");
    select.insertAdjacentElement("afterend", wrapper);

    const controller = {
      id,
      wrapper,
      trigger,
      value,
      menu,
      panel,
      overlayHost,
      activeIndex: select.selectedIndex,
      optionSignature: "",
      observer: null,
    };
    sweetSelectControllers.set(select, controller);

    trigger.addEventListener("click", () => {
      if (wrapper.classList.contains("is-open")) {
        closeSweetSelect(select);
      } else {
        openSweetSelect(select);
      }
    });
    trigger.addEventListener("keydown", (event) => handleSweetSelectKeydown(select, event));
    select.addEventListener("change", () => syncSweetSelect(select));
    controller.observer = new MutationObserver(() => syncSweetSelect(select));
    controller.observer.observe(select, {
      attributes: true,
      attributeFilter: ["disabled"],
      childList: true,
      subtree: true,
    });
    syncSweetSelect(select);
  }

  function initSweetSelects() {
    for (const select of selects) initSweetSelect(select);
    document.addEventListener("click", (event) => {
      for (const controller of sweetSelectControllers.values()) {
        if (controller.wrapper.contains(event.target)) return;
      }
      closeSweetSelects();
    });
    window.addEventListener("resize", updateOpenSweetSelectPlacements);
    window.addEventListener("scroll", updateOpenSweetSelectPlacements, { passive: true, capture: true });
  }

  function syncSweetSelects() {
    for (const select of sweetSelectControllers.keys()) syncSweetSelect(select);
  }

  function comboControlLabel(input) {
    const explicit = text(input.getAttribute("aria-label")).trim();
    if (explicit) return explicit;
    const label = input.closest("label");
    const caption = label
      ? [...label.children].find((child) => child.tagName === "SPAN")?.textContent
      : "";
    return text(caption).trim() || "候选项";
  }

  function comboClearLabel(input) {
    const explicit = text(input?.dataset?.emptyLabel).trim();
    if (explicit) return explicit;
    return input?.id === "cfgPersonaId" ? "不指定人设" : "跟随默认";
  }

  function comboListOptions(controller) {
    const list = controller?.list;
    const input = controller?.input;
    const options = [];
    const seen = new Set();

    function addOption(value, label) {
      const optionValue = text(value).trim();
      if (seen.has(optionValue)) return;
      seen.add(optionValue);
      options.push({
        value: optionValue,
        label: text(label).trim() || optionValue,
      });
    }

    if (input?.dataset?.comboOptional !== "false") {
      addOption("", comboClearLabel(input));
    }

    for (const option of [...(list?.options || [])]) {
      const value = text(option.value).trim();
      const label = text(option.label || option.textContent || option.value).trim();
      if (value || label) addOption(value, label);
    }

    return options;
  }

  function comboFilteredOptions(controller) {
    const query = text(controller.input.value).trim().toLowerCase();
    const options = comboListOptions(controller);
    const exactValueSelected = options.some((option) => option.value && option.value.toLowerCase() === query);
    if (!query || exactValueSelected) return options;
    return options.filter((option) => (
      option.value.toLowerCase().includes(query)
      || option.label.toLowerCase().includes(query)
    ));
  }

  function comboDisplayLabel(option) {
    return text(option.label || option.value).trim() || "跟随默认";
  }

  function comboOptionSubtitle(option) {
    const label = comboDisplayLabel(option);
    const value = text(option.value).trim();
    if (!value || value === label || label.includes(value)) return "";
    return value;
  }

  function buildSweetComboOption(input, option, index) {
    const controller = sweetComboControllers.get(input);
    const item = document.createElement("button");
    const title = document.createElement("strong");
    const subtitle = comboOptionSubtitle(option);
    item.type = "button";
    item.id = `${controller.id}-option-${index}`;
    item.className = "sweet-combo-option";
    item.dataset.index = String(index);
    item.dataset.value = option.value;
    item.role = "option";
    item.setAttribute("aria-selected", option.value === input.value ? "true" : "false");
    item.classList.toggle("is-selected", option.value === input.value);
    item.classList.toggle("is-clear", !option.value);
    title.textContent = comboDisplayLabel(option);
    item.append(title);
    if (subtitle) {
      const detail = document.createElement("span");
      detail.textContent = subtitle;
      item.append(detail);
    }
    item.addEventListener("click", () => commitSweetCombo(input, index));
    return item;
  }

  function setSweetComboActive(input, index) {
    const controller = sweetComboControllers.get(input);
    if (!controller) return;
    const items = [...controller.menu.querySelectorAll(".sweet-combo-option")];
    const nextIndex = items.length ? Math.min(Math.max(index, 0), items.length - 1) : -1;
    controller.activeIndex = nextIndex;
    for (const item of items) {
      const active = Number(item.dataset.index) === nextIndex;
      item.classList.toggle("is-active", active);
      if (active) {
        input.setAttribute("aria-activedescendant", item.id);
        if (!controller.menu.hidden) item.scrollIntoView({ block: "nearest" });
      }
    }
  }

  function renderSweetCombo(input) {
    const controller = sweetComboControllers.get(input);
    if (!controller) return;
    const options = comboFilteredOptions(controller);
    controller.options = options;
    if (!options.length) {
      const empty = document.createElement("div");
      empty.className = "sweet-combo-empty";
      empty.textContent = "没有匹配的候选项";
      controller.menu.replaceChildren(empty);
      setSweetComboActive(input, -1);
      return;
    }
    controller.menu.replaceChildren(...options.map((option, index) => buildSweetComboOption(input, option, index)));
    const selectedIndex = options.findIndex((option) => option.value === input.value);
    setSweetComboActive(input, selectedIndex >= 0 ? selectedIndex : 0);
  }

  function closeSweetCombo(input) {
    const controller = sweetComboControllers.get(input);
    if (!controller || !controller.wrapper.classList.contains("is-open")) return;
    controller.wrapper.classList.remove("is-open");
    clearSweetMenuPlacement(controller);
    controller.menu.hidden = true;
    input.setAttribute("aria-expanded", "false");
    input.removeAttribute("aria-activedescendant");
  }

  function closeSweetCombos(except = null) {
    closeSweetControlSet(sweetComboControllers, closeSweetCombo, except);
  }

  function updateSweetComboPlacement(input) {
    const controller = sweetComboControllers.get(input);
    if (controller) applySweetMenuPlacement(controller, input);
  }

  function updateOpenSweetComboPlacements() {
    for (const input of sweetComboControllers.keys()) updateSweetComboPlacement(input);
  }

  function openSweetCombo(input) {
    const controller = sweetComboControllers.get(input);
    if (!controller || input.disabled) return;
    closeSweetSelects();
    closeSweetCombos(input);
    renderSweetCombo(input);
    controller.wrapper.classList.add("is-open");
    controller.menu.hidden = false;
    input.setAttribute("aria-expanded", "true");
    updateSweetComboPlacement(input);
  }

  function moveSweetComboActive(input, step) {
    const controller = sweetComboControllers.get(input);
    const items = controller ? controller.menu.querySelectorAll(".sweet-combo-option") : [];
    if (!controller || !items.length) return;
    const current = controller.activeIndex >= 0 ? controller.activeIndex : 0;
    const next = (current + step + items.length) % items.length;
    setSweetComboActive(input, next);
  }

  function commitSweetCombo(input, index) {
    const controller = sweetComboControllers.get(input);
    const option = controller?.options?.[index];
    if (!controller || !option) return;
    const previous = input.value;
    input.value = option.value;
    renderSweetCombo(input);
    closeSweetCombo(input);
    input.focus({ preventScroll: true });
    if (input.value !== previous) {
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.dispatchEvent(new Event("change", { bubbles: true }));
    }
  }

  function handleSweetComboKeydown(input, event) {
    const controller = sweetComboControllers.get(input);
    if (!controller) return;
    const open = controller.wrapper.classList.contains("is-open");
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (!open) openSweetCombo(input);
      moveSweetComboActive(input, event.key === "ArrowDown" ? 1 : -1);
      return;
    }
    if (event.key === "Enter" && open && controller.activeIndex >= 0) {
      event.preventDefault();
      commitSweetCombo(input, controller.activeIndex);
      return;
    }
    if (event.key === "Escape" && open) {
      event.preventDefault();
      closeSweetCombo(input);
    }
  }

  function syncSweetCombo(input) {
    const controller = sweetComboControllers.get(input);
    if (!controller) return;
    if (controller.wrapper.classList.contains("is-open")) {
      renderSweetCombo(input);
      updateSweetComboPlacement(input);
    }
  }

  function initSweetCombo(input, list) {
    if (!input || !list || sweetComboControllers.has(input)) return;
    const id = `sweet-combo-${input.id || sweetComboControllers.size}`;
    const wrapper = document.createElement("div");
    const menu = document.createElement("div");
    wrapper.className = "sweet-combo";
    wrapper.dataset.comboFor = input.id || "";
    menu.id = `${id}-listbox`;
    menu.className = "sweet-combo-menu";
    menu.role = "listbox";
    menu.hidden = true;

    input.removeAttribute("list");
    input.setAttribute("role", "combobox");
    input.setAttribute("aria-autocomplete", "list");
    input.setAttribute("aria-expanded", "false");
    input.setAttribute("aria-controls", menu.id);
    input.setAttribute("aria-label", comboControlLabel(input));
    input.insertAdjacentElement("beforebegin", wrapper);
    wrapper.append(input, menu);

    sweetComboControllers.set(input, {
      id,
      input,
      list,
      wrapper,
      menu,
      options: [],
      activeIndex: -1,
      pointerDownOpen: false,
      pointerDown: false,
    });

    input.addEventListener("pointerdown", () => {
      const controller = sweetComboControllers.get(input);
      if (!controller) return;
      controller.pointerDown = true;
      controller.pointerDownOpen = controller.wrapper.classList.contains("is-open");
    });
    input.addEventListener("focus", () => {
      const controller = sweetComboControllers.get(input);
      if (!controller?.pointerDown) openSweetCombo(input);
    });
    input.addEventListener("click", () => {
      const controller = sweetComboControllers.get(input);
      if (!controller) return;
      if (controller.pointerDownOpen) {
        closeSweetCombo(input);
      } else {
        openSweetCombo(input);
      }
      controller.pointerDown = false;
      controller.pointerDownOpen = false;
    });
    input.addEventListener("input", () => syncSweetCombo(input));
    input.addEventListener("keydown", (event) => handleSweetComboKeydown(input, event));
  }

  function initSweetCombos() {
    for (const combo of combos) initSweetCombo(combo?.input, combo?.list);
    document.addEventListener("click", (event) => {
      for (const controller of sweetComboControllers.values()) {
        if (controller.wrapper.contains(event.target)) return;
      }
      closeSweetCombos();
    });
    window.addEventListener("resize", updateOpenSweetComboPlacements);
    window.addEventListener("scroll", updateOpenSweetComboPlacements, { passive: true, capture: true });
  }

  return {
    closeSweetSelects,
    initSweetCombos,
    initSweetSelects,
    syncSweetCombo,
    syncSweetSelect,
    syncSweetSelects,
  };
}
