// Mobile nav toggle
const menuBtn = document.getElementById('mobile-menu-btn');
const mobileMenu = document.getElementById('mobile-menu');

if (menuBtn && mobileMenu) {
  menuBtn.addEventListener('click', () => {
    const isOpen = !mobileMenu.classList.contains('hidden');
    mobileMenu.classList.toggle('hidden');
    // Swap hamburger / close icon
    menuBtn.querySelector('.icon-open').classList.toggle('hidden', !isOpen);
    menuBtn.querySelector('.icon-close').classList.toggle('hidden', isOpen);
  });
}

// Sticky header backdrop blur on scroll
const header = document.getElementById('site-header');
if (header) {
  window.addEventListener('scroll', () => {
    if (window.scrollY > 10) {
      header.classList.add('bg-white/80', 'backdrop-blur-lg', 'shadow-sm');
      header.classList.remove('bg-white');
    } else {
      header.classList.remove('bg-white/80', 'backdrop-blur-lg', 'shadow-sm');
      header.classList.add('bg-white');
    }
  });
}

// Smooth scroll for anchor links
document.querySelectorAll('a[href^="#"]').forEach(link => {
  link.addEventListener('click', (e) => {
    const target = document.querySelector(link.getAttribute('href'));
    if (target) {
      e.preventDefault();
      target.scrollIntoView({ behavior: 'smooth' });
    }
  });
});

// FAQ accordion toggle
document.querySelectorAll('[data-faq-btn]').forEach(btn => {
  btn.addEventListener('click', () => {
    const content = btn.nextElementSibling;
    const icon = btn.querySelector('[data-faq-icon]');
    const isOpen = !content.classList.contains('hidden');
    // Close all
    document.querySelectorAll('[data-faq-btn]').forEach(other => {
      other.nextElementSibling.classList.add('hidden');
      other.querySelector('[data-faq-icon]').classList.remove('rotate-180');
    });
    // Toggle clicked
    if (!isOpen) {
      content.classList.remove('hidden');
      icon.classList.add('rotate-180');
    }
  });
});

// Fade-in on scroll using IntersectionObserver
const fadeElements = document.querySelectorAll('.fade-in-section');
// Also auto-add fade class to all main sections (except first/hero)
const mainSections = document.querySelectorAll('main > section');
mainSections.forEach((el, i) => {
  if (i > 0) el.classList.add('fade-in-section');
});

const allFadeEls = document.querySelectorAll('.fade-in-section');
if (allFadeEls.length && 'IntersectionObserver' in window) {
  const observer = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        entry.target.classList.add('is-visible');
        observer.unobserve(entry.target);
      }
    });
  }, { threshold: 0.08 });

  allFadeEls.forEach(el => observer.observe(el));
}

// Hero infographic — animate on page load with a short delay so users see it
const heroAnim = document.querySelector('[data-animate-hero]');
if (heroAnim) {
  setTimeout(() => heroAnim.classList.add('is-animated'), 400);
}

// Infographic animations on scroll — adds 'is-animated' to [data-animate] elements
const animateEls = document.querySelectorAll('[data-animate]');
if (animateEls.length && 'IntersectionObserver' in window) {
  const animObserver = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        entry.target.classList.add('is-animated');
        animObserver.unobserve(entry.target);
      }
    });
  }, { threshold: 0.15 });

  animateEls.forEach(el => animObserver.observe(el));
}
