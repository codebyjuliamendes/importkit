// Applies the saved theme before first paint (loaded synchronously in <head>; CSP forbids inline).
(function () {
  try {
    var saved = localStorage.getItem('importkit-theme');
    if (saved === 'light' || saved === 'dark') document.documentElement.dataset.theme = saved;
  } catch (e) { /* storage unavailable: follow the OS preference */ }
})();
