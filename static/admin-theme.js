(function () {
  var storageKey = "schoolBotAdminTheme";
  var root = document.documentElement;

  function getTheme() {
    return root.getAttribute("data-admin-theme") || "light";
  }

  function applyTheme(theme) {
    root.setAttribute("data-admin-theme", theme);
    root.setAttribute("data-bs-theme", theme);
    if (document.body) {
      document.body.setAttribute("data-bs-theme", theme);
    }
    localStorage.setItem(storageKey, theme);

    document.querySelectorAll("[data-theme-label]").forEach(function (label) {
      label.textContent = theme === "dark" ? "Темна тема" : "Світла тема";
    });
  }

  applyTheme(getTheme());

  document.querySelectorAll("[data-theme-toggle]").forEach(function (button) {
    button.addEventListener("click", function () {
      applyTheme(getTheme() === "dark" ? "light" : "dark");
    });
  });
})();
