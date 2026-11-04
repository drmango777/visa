// Language switch for the simple text pages (Privacy, Terms).
// Each page has one <div data-lang-block="en|es|ht"> per language. The choice is shared with the main site.
(function () {
  var langs = ["en", "es", "ht"], lang = "en";
  try { var s = localStorage.getItem("sailby-lang"); if (langs.indexOf(s) >= 0) lang = s; } catch (e) { /* storage blocked */ }
  function apply(l) {
    lang = l;
    document.documentElement.lang = l;
    document.querySelectorAll("[data-lang-block]").forEach(function (el) { el.hidden = el.getAttribute("data-lang-block") !== l; });
    document.querySelectorAll("[data-lang]").forEach(function (b) { b.setAttribute("aria-pressed", String(b.getAttribute("data-lang") === l)); });
    var title = document.querySelector("[data-lang-block='" + l + "'] h1");
    if (title) document.title = title.textContent + " | Sail-by";
  }
  document.querySelectorAll("[data-lang]").forEach(function (b) {
    b.addEventListener("click", function () {
      apply(b.getAttribute("data-lang"));
      try { localStorage.setItem("sailby-lang", lang); } catch (e) { /* not saved */ }
    });
  });
  apply(lang);
})();
