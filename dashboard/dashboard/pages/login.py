"""Login page — splash gate for the dashboard."""

import reflex as rx
from ..state import AppState


def login_page() -> rx.Component:
    """Centered splash gate with particles background."""
    return rx.box(
        # Particle canvas
        rx.el.canvas(
            id="particle-canvas",
            style={
                "position": "fixed",
                "inset": "0",
                "width": "100%",
                "height": "100%",
                "zIndex": "0",
            },
        ),
        # Content
        rx.center(
            rx.vstack(
                rx.image(
                    src="/tslz-avatar.jpg",
                    width="84px",
                    height="84px",
                    border_radius="999px",
                    object_fit="cover",
                    border="2px solid rgba(99,102,241,0.35)",
                    box_shadow="0 0 24px rgba(99,102,241,0.25)",
                ),
                rx.text(
                    "Tslz",
                    font_size="1.4rem",
                    font_weight="700",
                    color="#f8fafc",
                    letter_spacing="0.02em",
                ),
                rx.text(
                    "Big Money moves - Mr. Grise",
                    font_size="0.85rem",
                    font_weight="700",
                    color="#ffffff",
                ),
                rx.form(
                    rx.button(
                        "Enter",
                        type="submit",
                        width="180px",
                        height="44px",
                        background="rgba(15,23,42,0.9)",
                        color="#e2e8f0",
                        font_weight="600",
                        font_size="0.9rem",
                        border="1px solid rgba(59,130,246,0.8)",
                        border_radius="12px",
                        cursor="pointer",
                        box_shadow="0 0 18px rgba(59,130,246,0.35)",
                        _hover={"box_shadow": "0 0 26px rgba(59,130,246,0.55)"},
                    ),
                    on_submit=AppState.enter_app,
                ),
                rx.cond(
                    AppState.login_error != "",
                    rx.text(
                        AppState.login_error,
                        font_size="0.78rem",
                        color="#ef4444",
                    ),
                ),
                spacing="2",
                align="center",
                padding="36px 48px",
                background="rgba(2,6,23,0.6)",
                border="1px solid rgba(59,130,246,0.18)",
                border_radius="20px",
                box_shadow="0 30px 60px rgba(0,0,0,0.5)",
                z_index="1",
            ),
            width="100%",
            height="100vh",
            z_index="1",
            style={"animation": "fade-slide-in 0.7s ease-out both"},
        ),
        rx.script("""
            (function() {
              const canvas = document.getElementById('particle-canvas');
              if (!canvas) return;
              const ctx = canvas.getContext('2d');
              let w, h, particles;
              const mouse = { x: -9999, y: -9999, r: 120 };

              function resize() {
                w = canvas.width = window.innerWidth;
                h = canvas.height = window.innerHeight;
              }
              function init() {
                particles = [];
                const count = Math.min(160, Math.floor((w * h) / 11000));
                for (let i = 0; i < count; i++) {
                  particles.push({
                    x: Math.random() * w,
                    y: Math.random() * h,
                    vx: (Math.random() - 0.5) * 0.45,
                    vy: (Math.random() - 0.5) * 0.45,
                    r: Math.random() * 1.8 + 0.6,
                    a: Math.random() * 0.5 + 0.2
                  });
                }
              }
              function step() {
                ctx.clearRect(0, 0, w, h);
                ctx.fillStyle = '#0a0a0a';
                ctx.fillRect(0, 0, w, h);
                for (let i = 0; i < particles.length; i++) {
                  const p = particles[i];
                  // Mouse repulsion
                  const dx = p.x - mouse.x;
                  const dy = p.y - mouse.y;
                  const dist = Math.sqrt(dx * dx + dy * dy);
                  if (dist < mouse.r) {
                    const force = (mouse.r - dist) / mouse.r;
                    p.vx += (dx / (dist || 1)) * force * 0.6;
                    p.vy += (dy / (dist || 1)) * force * 0.6;
                  }
                  p.x += p.vx; p.y += p.vy;
                  p.vx *= 0.98; p.vy *= 0.98;
                  if (p.x < 0 || p.x > w) p.vx *= -1;
                  if (p.y < 0 || p.y > h) p.vy *= -1;
                  ctx.beginPath();
                  ctx.fillStyle = `rgba(59,130,246,${p.a})`;
                  ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
                  ctx.fill();
                }
                requestAnimationFrame(step);
              }
              resize(); init(); step();
              window.addEventListener('resize', () => { resize(); init(); });
              window.addEventListener('mousemove', (e) => { mouse.x = e.clientX; mouse.y = e.clientY; });
              window.addEventListener('mouseleave', () => { mouse.x = -9999; mouse.y = -9999; });
              window.addEventListener('touchmove', (e) => {
                if (!e.touches || !e.touches[0]) return;
                mouse.x = e.touches[0].clientX;
                mouse.y = e.touches[0].clientY;
              }, { passive: true });
              window.addEventListener('touchend', () => { mouse.x = -9999; mouse.y = -9999; });
            })();
        """),
        width="100%",
        height="100vh",
        background="#0a0a0a",
    )
