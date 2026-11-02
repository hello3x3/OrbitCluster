// Presentation only: mobile drawer and account menu. No business requests.
(() => {
  const sidebar = document.getElementById('sidebar');
  const toggle = document.getElementById('sidebar-toggle');
  const backdrop = document.getElementById('sidebar-backdrop');
  const account = document.getElementById('account-menu');
  const mobile = matchMedia('(max-width:900px)');
  function setOpen(open, restore = true) {
    open = Boolean(open && mobile.matches);
    document.body.classList.toggle('sidebar-open', open);
    toggle.setAttribute('aria-expanded', String(open));
    backdrop.hidden = !open;
    if (open) {
      sidebar.setAttribute('role', 'dialog');
      sidebar.setAttribute('aria-modal', 'true');
      sidebar.querySelector('[data-sidebar-close]').focus();
    } else {
      sidebar.removeAttribute('role');
      sidebar.removeAttribute('aria-modal');
      if (restore && mobile.matches) toggle.focus();
    }
  }
  toggle.addEventListener('click', () => setOpen(!document.body.classList.contains('sidebar-open')));
  sidebar.querySelector('[data-sidebar-close]').addEventListener('click', () => setOpen(false));
  backdrop.addEventListener('click', () => setOpen(false));
  mobile.addEventListener('change', () => setOpen(false, false));
  sidebar.addEventListener('click', event => { if (event.target.closest('a') && mobile.matches) setOpen(false, false); });
  document.addEventListener('click', event => { if (account && !account.contains(event.target)) account.open = false; });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape') {
      if (document.body.classList.contains('sidebar-open')) setOpen(false);
      if (account?.open) { account.open = false; account.querySelector('summary').focus(); }
    }
    if (event.key === 'Tab' && document.body.classList.contains('sidebar-open')) {
      const controls = [...sidebar.querySelectorAll('a,summary,button')].filter(e => e.getClientRects().length);
      const first = controls[0], last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    }
  });
})();
