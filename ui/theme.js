// Applies the saved colour theme, light/dark mode and text size before the page is drawn
// (loaded without "defer", so the page never flashes in the wrong colours). The settings
// menu in app.js changes them through applyLook().
(function () {
  const get = key => { try { return localStorage.getItem(key); } catch (e) { return null; } };
  const root = document.documentElement;
  const systemDark = window.matchMedia("(prefers-color-scheme: dark)");
  window.applyLook = function () {
    const mode = get("themeMode") || "system";
    root.dataset.palette = get("palette") || "blue";
    root.dataset.size = get("textSize") || "m";
    root.dataset.themeMode = mode;
    root.classList.toggle("dark", mode === "dark" || (mode === "system" && systemDark.matches));
  };
  window.applyLook();
  systemDark.addEventListener("change", window.applyLook);
})();
