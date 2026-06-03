/* PFactory Pages — animations + counters. Vanilla, no deps. */
(function () {
  'use strict';
  var reducedMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  function setupReveals() {
    var els = document.querySelectorAll('.reveal');
    if (!els.length) return;
    if (reducedMotion || !('IntersectionObserver' in window)) {
      els.forEach(function (el) { el.classList.add('reveal--visible'); });
      return;
    }
    var observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) {
          entry.target.classList.add('reveal--visible');
          observer.unobserve(entry.target);
        }
      });
    }, { threshold: 0.12, rootMargin: '0px 0px -50px 0px' });
    els.forEach(function (el) { observer.observe(el); });
  }

  function easeOutCubic(t) { return 1 - Math.pow(1 - t, 3); }

  function animateCount(el, target, duration) {
    if (reducedMotion) { el.textContent = target.toLocaleString('en-US'); return; }
    var start = performance.now();
    function tick(now) {
      var t = Math.min(1, (now - start) / duration);
      el.textContent = Math.round(easeOutCubic(t) * target).toLocaleString('en-US');
      if (t < 1) requestAnimationFrame(tick);
    }
    requestAnimationFrame(tick);
  }

  function setupCounters() {
    var els = document.querySelectorAll('[data-counter]');
    if (!els.length) return;
    if (!('IntersectionObserver' in window)) {
      els.forEach(function (el) {
        animateCount(el, parseInt(el.dataset.counter, 10) || 0, 1400);
      });
      return;
    }
    var observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) {
          animateCount(entry.target, parseInt(entry.target.dataset.counter, 10) || 0, 1400);
          observer.unobserve(entry.target);
        }
      });
    }, { threshold: 0.5 });
    els.forEach(function (el) { el.textContent = '0'; observer.observe(el); });
  }

  function boot() { setupReveals(); setupCounters(); }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else { boot(); }
})();
