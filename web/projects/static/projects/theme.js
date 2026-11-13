/* Applies the saved theme before the first paint. Light is the default; dark is the control-room look. */
(() => {
  try {
    const saved = localStorage.getItem("athena-theme");
    document.documentElement.dataset.theme = saved === "dark" ? "dark" : "light";
  } catch (error) {
    document.documentElement.dataset.theme = "light";
  }
})();
