(function () {
  'use strict';

  const themeKey = 'novelforge-theme';
  const root = document.documentElement;
  const sidebar = document.getElementById('studio-sidebar');
  const navToggle = document.getElementById('mobile-nav-toggle');
  const navBackdrop = document.getElementById('mobile-nav-backdrop');
  const themeToggle = document.createElement('button');
  const themeLabel = document.createElement('span');

  themeToggle.className = 'theme-toggle';
  themeToggle.id = 'theme-toggle';
  themeToggle.type = 'button';
  themeToggle.setAttribute('aria-label', '切换到浅色主题');
  themeToggle.innerHTML = '<span class="theme-glyph" aria-hidden="true"></span>';
  themeLabel.id = 'theme-toggle-label';
  themeToggle.appendChild(themeLabel);
  document.querySelector('.sidebar-footer')?.appendChild(themeToggle);

  function setTheme(theme) {
    const nextTheme = theme === 'light' ? 'light' : 'dark';
    root.dataset.theme = nextTheme;
    root.style.colorScheme = nextTheme;
    try { localStorage.setItem(themeKey, nextTheme); } catch (_) { /* storage is optional */ }
    const isLight = nextTheme === 'light';
    themeToggle.setAttribute('aria-label', isLight ? '切换到深色主题' : '切换到浅色主题');
    themeToggle.setAttribute('aria-pressed', String(isLight));
    themeLabel.textContent = isLight ? '深色' : '浅色';
  }

  function setNavOpen(open) {
    const isOpen = Boolean(open);
    sidebar?.classList.toggle('is-open', isOpen);
    navBackdrop?.classList.toggle('is-visible', isOpen);
    navToggle?.setAttribute('aria-expanded', String(isOpen));
    document.body.classList.toggle('nav-open', isOpen);
  }

  setTheme(root.dataset.theme || 'light');

  themeToggle.addEventListener('click', function () {
    setTheme(root.dataset.theme === 'light' ? 'dark' : 'light');
  });

  navToggle?.addEventListener('click', function () {
    setNavOpen(!sidebar?.classList.contains('is-open'));
  });

  navBackdrop?.addEventListener('click', function () { setNavOpen(false); });
  sidebar?.addEventListener('click', function (event) {
    if (event.target.closest('.nav-item')) setNavOpen(false);
  });
  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape') setNavOpen(false);
  });
})();
