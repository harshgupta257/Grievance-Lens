// Sets the colour theme before the page paints (loaded in <head>, so there is no flash of the wrong theme).
// The visitor's choice is kept in localStorage; without one, the system setting is followed. The toggle
// button itself lives in site.js.
(() => {
  let theme = null;
  try { theme = localStorage.getItem("theme"); } catch (e) { /* storage blocked: follow the system */ }
  if (theme !== "light" && theme !== "dark") {
    theme = window.matchMedia && matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  document.documentElement.dataset.theme = theme;
})();
