// Language switch for the simple pages (Privacy, Terms, 404).
// Long text: one <div data-lang-block="en|es|ht"> per language.
// Short text (the heading, links): one element with data-en / data-es / data-ht.
// The choice is shared with the main site.
(function () {
  var langs = ["en", "es", "ht"], lang = "en";
  try { var s = localStorage.getItem("sailby-lang"); if (langs.indexOf(s) >= 0) lang = s; } catch (e) { /* storage blocked */ }
  function apply(l) {
    lang = l;
    document.documentElement.lang = l;
    document.querySelectorAll("[data-lang-block]").forEach(function (el) { el.hidden = el.getAttribute("data-lang-block") !== l; });
    document.querySelectorAll("[data-" + l + "]").forEach(function (el) { el.textContent = el.getAttribute("data-" + l); });
    document.querySelectorAll("[data-lang]").forEach(function (b) { b.setAttribute("aria-pressed", String(b.getAttribute("data-lang") === l)); });
    var h1 = document.querySelector("h1");
    if (h1) document.title = h1.textContent + " | Sail-by";
  }
  document.querySelectorAll("[data-lang]").forEach(function (b) {
    b.addEventListener("click", function () {
      apply(b.getAttribute("data-lang"));
      try { localStorage.setItem("sailby-lang", lang); } catch (e) { /* not saved */ }
    });
  });
  apply(lang);
})();
