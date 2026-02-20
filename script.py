import os
import math
import time
from dataclasses import dataclass
from typing import List, Tuple, Optional

import pygame
from PIL import Image, ImageFilter, ImageEnhance

# =========================
# Premium Book Album (Pygame)
# - Smooth page flip
# - Mouse drag flip
# - Keyboard controls
# - Dynamic shadow + paper sheen
# - Glass UI HUD
# - Autoplay
# - Hi-res downscale + caching
# =========================

# ---------- Config ----------
APP_TITLE = "Premium Book Album"
FPS = 60

WINDOW_W, WINDOW_H = 1200, 720
BOOK_MARGIN = 60
SPINE_W = 18
PAPER_COLOR = (245, 242, 235)

BG_TOP = (10, 18, 32)
BG_BOTTOM = (18, 28, 55)

HUD_BG = (255, 255, 255, 32)
HUD_BORDER = (255, 255, 255, 60)
HUD_TEXT = (245, 245, 245)

PAGE_GAP = 2
MAX_PAGES = 500

# Flip physics
FLIP_SPEED = 10.0     # for keyboard flips
DRAG_SPRING = 22.0    # for mouse release spring
DRAG_DAMP = 0.86
SHADOW_MAX = 120      # shadow intensity

# Image processing
MAX_TEX_W = 900
MAX_TEX_H = 900

# ---------- Helpers ----------
def clamp(x, a, b):
    return max(a, min(b, x))

def lerp(a, b, t):
    return a + (b - a) * t

def smoothstep(t):
    t = clamp(t, 0.0, 1.0)
    return t * t * (3 - 2 * t)

def ease_out_cubic(t):
    t = clamp(t, 0.0, 1.0)
    return 1 - (1 - t) ** 3

def gradient_surface(size, top_color, bottom_color):
    w, h = size
    surf = pygame.Surface((w, h), pygame.SRCALPHA)
    for y in range(h):
        t = y / max(1, h - 1)
        r = int(lerp(top_color[0], bottom_color[0], t))
        g = int(lerp(top_color[1], bottom_color[1], t))
        b = int(lerp(top_color[2], bottom_color[2], t))
        pygame.draw.line(surf, (r, g, b), (0, y), (w, y))
    return surf

def make_rounded_rect(size, radius, color, border_color=None, border=0, alpha=255):
    w, h = size
    surf = pygame.Surface((w, h), pygame.SRCALPHA)
    rect = pygame.Rect(0, 0, w, h)
    pygame.draw.rect(surf, (*color[:3], alpha), rect, border_radius=radius)
    if border_color and border > 0:
        pygame.draw.rect(surf, (*border_color[:3], alpha), rect, border, border_radius=radius)
    return surf

def draw_text(surf, text, font, pos, color=(255,255,255), shadow=True):
    x, y = pos
    if shadow:
        sh = font.render(text, True, (0,0,0))
        sh.set_alpha(140)
        surf.blit(sh, (x+2, y+2))
    img = font.render(text, True, color)
    surf.blit(img, (x, y))

def pil_to_surface(pil_img: Image.Image) -> pygame.Surface:
    mode = pil_img.mode
    data = pil_img.tobytes()
    size = pil_img.size
    return pygame.image.fromstring(data, size, mode).convert_alpha()

def load_and_process_image(path, target_size: Tuple[int,int]) -> pygame.Surface:
    # High quality downscale + mild sharpening
    img = Image.open(path).convert("RGBA")

    # Fit to target size keeping aspect
    tw, th = target_size
    iw, ih = img.size
    scale = min(tw/iw, th/ih)
    nw, nh = max(1, int(iw*scale)), max(1, int(ih*scale))
    img = img.resize((nw, nh), Image.LANCZOS)

    # Enhance slightly for "premium"
    img = ImageEnhance.Contrast(img).enhance(1.06)
    img = ImageEnhance.Color(img).enhance(1.04)
    img = ImageEnhance.Sharpness(img).enhance(1.10)

    # Put on paper background (centered)
    canvas = Image.new("RGBA", (tw, th), (*PAPER_COLOR, 255))
    ox = (tw - nw)//2
    oy = (th - nh)//2

    # Soft drop shadow behind photo
    shadow = Image.new("RGBA", (nw, nh), (0,0,0,180)).filter(ImageFilter.GaussianBlur(10))
    canvas.alpha_composite(shadow, (ox+6, oy+8))
    canvas.alpha_composite(img, (ox, oy))

    return pil_to_surface(canvas)

def list_page_files(folder="pages") -> List[str]:
    if not os.path.isdir(folder):
        return []
    exts = (".jpg", ".jpeg", ".png", ".webp", ".bmp")
    files = [os.path.join(folder, f) for f in os.listdir(folder) if f.lower().endswith(exts)]
    files.sort()
    return files[:MAX_PAGES]

# ---------- Page Cache ----------
class PageCache:
    def __init__(self, page_size):
        self.page_size = page_size
        self.cache = {}

    def get(self, path) -> pygame.Surface:
        key = (path, self.page_size)
        if key in self.cache:
            return self.cache[key]
        surf = load_and_process_image(path, self.page_size)
        self.cache[key] = surf
        return surf

# ---------- Data ----------
@dataclass
class FlipState:
    flipping: bool = False
    direction: int = 0     # +1 next, -1 prev
    t: float = 0.0         # 0..1 animation progress
    dragging: bool = False
    drag_x: float = 0.0    # current drag x
    vel: float = 0.0       # spring velocity for release

# ---------- Book Renderer ----------
class BookAlbum:
    def __init__(self, screen: pygame.Surface, pages: List[str]):
        self.screen = screen
        self.pages = pages[:]  # list of image paths
        self.index = 0         # left page index (even)
        self.flip = FlipState()

        self.font = pygame.font.SysFont("Segoe UI", 18)
        self.font_big = pygame.font.SysFont("Segoe UI", 26, bold=True)

        # Book geometry
        self.book_rect = pygame.Rect(
            BOOK_MARGIN,
            BOOK_MARGIN,
            WINDOW_W - 2*BOOK_MARGIN,
            WINDOW_H - 2*BOOK_MARGIN
        )

        self.spread_rect = self.book_rect.inflate(-40, -40)
        self.spine_x = self.spread_rect.centerx

        self.page_w = (self.spread_rect.width - SPINE_W - PAGE_GAP*2)//2
        self.page_h = self.spread_rect.height

        self.left_rect = pygame.Rect(self.spread_rect.left, self.spread_rect.top, self.page_w, self.page_h)
        self.right_rect = pygame.Rect(self.spine_x + SPINE_W//2 + PAGE_GAP, self.spread_rect.top, self.page_w, self.page_h)

        self.cache = PageCache((self.page_w, self.page_h))

        # UI state
        self.autoplay = False
        self.autoplay_interval = 2.2
        self._autoplay_timer = 0.0

        # Pre-make background gradient
        self.bg = gradient_surface((WINDOW_W, WINDOW_H), BG_TOP, BG_BOTTOM)

        # Lighting overlay (subtle noise + vignette)
        self.vignette = self.make_vignette((WINDOW_W, WINDOW_H))

    def make_vignette(self, size):
        w, h = size
        surf = pygame.Surface((w, h), pygame.SRCALPHA)
        center = (w/2, h/2)
        maxd = math.hypot(w/2, h/2)
        for y in range(h):
            for x in range(w):
                d = math.hypot(x-center[0], y-center[1]) / maxd
                a = int(clamp((d**1.8) * 190, 0, 190))
                surf.set_at((x, y), (0,0,0,a))
        return surf

    def has_left(self):
        return self.index >= 0 and self.index < len(self.pages)

    def has_right(self):
        return (self.index + 1) >= 0 and (self.index + 1) < len(self.pages)

    def can_next(self):
        return self.index + 2 < len(self.pages)

    def can_prev(self):
        return self.index - 2 >= 0

    def get_page_surface(self, idx) -> pygame.Surface:
        if idx < 0 or idx >= len(self.pages):
            # blank paper page
            blank = pygame.Surface((self.page_w, self.page_h), pygame.SRCALPHA)
            blank.fill((*PAPER_COLOR, 255))
            return blank
        return self.cache.get(self.pages[idx])

    def start_flip(self, direction: int):
        if self.flip.flipping or self.flip.dragging:
            return
        if direction == 1 and not self.can_next():
            return
        if direction == -1 and not self.can_prev():
            return

        self.flip = FlipState(flipping=True, direction=direction, t=0.0, dragging=False, drag_x=0.0, vel=0.0)

    def start_drag(self, mx):
        # Only allow drag when cursor is near outer edges
        if self.flip.flipping:
            return
        # Right-to-left: next page drag from right page outer edge
        if self.can_next() and self.right_rect.collidepoint(mx, pygame.mouse.get_pos()[1]):
            if mx > self.right_rect.right - 80:
                self.flip.dragging = True
                self.flip.direction = 1
                self.flip.drag_x = mx
                self.flip.vel = 0.0
                return
        # Left-to-right: prev page drag from left page outer edge
        if self.can_prev() and self.left_rect.collidepoint(mx, pygame.mouse.get_pos()[1]):
            if mx < self.left_rect.left + 80:
                self.flip.dragging = True
                self.flip.direction = -1
                self.flip.drag_x = mx
                self.flip.vel = 0.0
                return

    def end_drag(self):
        if not self.flip.dragging:
            return
        # Decide commit or snap back based on drag threshold
        commit = False
        if self.flip.direction == 1:
            # dragged past spine => commit
            commit = self.flip.drag_x < self.spine_x
        else:
            commit = self.flip.drag_x > self.spine_x

        self.flip.dragging = False
        self.flip.flipping = True
        # Convert drag_x into t start
        self.flip.t = self.drag_to_t(self.flip.drag_x, self.flip.direction)
        # If commit: animate to end, else animate back to 0
        self.flip.vel = 0.0
        self._drag_commit = commit

    def drag_to_t(self, drag_x, direction):
        # Map drag_x to t (0..1)
        if direction == 1:
            # start at right edge -> spine -> left edge
            start = self.right_rect.right
            end = self.left_rect.left
            t = (start - drag_x) / max(1, (start - end))
        else:
            start = self.left_rect.left
            end = self.right_rect.right
            t = (drag_x - start) / max(1, (end - start))
        return clamp(t, 0.0, 1.0)

    def update(self, dt: float):
        # Autoplay
        if self.autoplay and not self.flip.flipping and not self.flip.dragging:
            self._autoplay_timer += dt
            if self._autoplay_timer >= self.autoplay_interval:
                self._autoplay_timer = 0.0
                if self.can_next():
                    self.start_flip(1)
                else:
                    self.index = 0  # loop

        # Dragging follow
        if self.flip.dragging:
            mx, _ = pygame.mouse.get_pos()
            self.flip.drag_x = clamp(mx, self.left_rect.left, self.right_rect.right)
            return

        # Flipping animation
        if self.flip.flipping:
            if hasattr(self, "_drag_commit"):
                # spring animation to target
                target = 1.0 if self._drag_commit else 0.0
                # spring towards target
                a = (target - self.flip.t) * DRAG_SPRING
                self.flip.vel = (self.flip.vel + a * dt) * DRAG_DAMP
                self.flip.t += self.flip.vel * dt
                if abs(target - self.flip.t) < 0.002 and abs(self.flip.vel) < 0.01:
                    self.flip.t = target
                    delattr(self, "_drag_commit")
                    self.finish_flip()
            else:
                self.flip.t += dt * (FLIP_SPEED / 10.0)
                if self.flip.t >= 1.0:
                    self.flip.t = 1.0
                    self.finish_flip()

    def finish_flip(self):
        # If reached end: apply index shift
        if self.flip.t >= 1.0:
            if self.flip.direction == 1:
                self.index += 2
            elif self.flip.direction == -1:
                self.index -= 2

        self.flip = FlipState()

    def handle_event(self, e: pygame.event.Event):
        if e.type == pygame.KEYDOWN:
            if e.key in (pygame.K_RIGHT, pygame.K_d, pygame.K_SPACE):
                self.start_flip(1)
            elif e.key in (pygame.K_LEFT, pygame.K_a, pygame.K_BACKSPACE):
                self.start_flip(-1)
            elif e.key == pygame.K_HOME:
                self.index = 0
            elif e.key == pygame.K_END:
                # jump to last even
                if len(self.pages) > 0:
                    self.index = (len(self.pages) - 1) // 2 * 2
                    if self.index >= len(self.pages):
                        self.index = max(0, len(self.pages) - 2)
            elif e.key == pygame.K_p:
                self.autoplay = not self.autoplay
                self._autoplay_timer = 0.0

        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            mx, my = e.pos
            self.start_drag(mx)

        if e.type == pygame.MOUSEBUTTONUP and e.button == 1:
            self.end_drag()

    def draw_book_shell(self):
        # Outer book card shadow
        shadow = pygame.Surface((self.book_rect.width+40, self.book_rect.height+40), pygame.SRCALPHA)
        pygame.draw.rect(shadow, (0,0,0,120), shadow.get_rect(), border_radius=34)
        shadow = pygame.transform.smoothscale(shadow, (shadow.get_width(), shadow.get_height()))
        self.screen.blit(shadow, (self.book_rect.x-20, self.book_rect.y-12))

        # Book cover
        cover = make_rounded_rect(self.book_rect.size, 28, (255,255,255), border_color=(255,255,255), border=2, alpha=26)
        self.screen.blit(cover, self.book_rect.topleft)

        # Inner spread base
        inner = pygame.Surface(self.spread_rect.size, pygame.SRCALPHA)
        pygame.draw.rect(inner, (255,255,255,18), inner.get_rect(), border_radius=22)
        self.screen.blit(inner, self.spread_rect.topleft)

        # Spine
        spine_rect = pygame.Rect(self.spine_x - SPINE_W//2, self.spread_rect.top, SPINE_W, self.spread_rect.height)
        pygame.draw.rect(self.screen, (0,0,0,75), spine_rect, border_radius=10)

    def draw_page(self, page_surf: pygame.Surface, dest_rect: pygame.Rect):
        # paper base behind
        paper = pygame.Surface((dest_rect.width, dest_rect.height), pygame.SRCALPHA)
        paper.fill((*PAPER_COLOR, 255))

        # subtle paper grain via alpha dots (cheap)
        for y in range(0, dest_rect.height, 8):
            for x in range(0, dest_rect.width, 8):
                a = 5 + ((x*y) % 7)
                paper.fill((255,255,255,a), rect=pygame.Rect(x, y, 1, 1))

        # page border
        pygame.draw.rect(paper, (0,0,0,18), paper.get_rect(), 2, border_radius=10)

        # content
        paper.blit(page_surf, (0,0))
        self.screen.blit(paper, dest_rect.topleft)

    def draw_shadow_band(self, rect: pygame.Rect, side: str, strength: int):
        # side: "left" or "right"
        strength = int(clamp(strength, 0, 255))
        band_w = min(90, rect.width)
        surf = pygame.Surface((band_w, rect.height), pygame.SRCALPHA)
        for x in range(band_w):
            t = x / max(1, band_w-1)
            if side == "left":
                a = int((1 - t)**1.6 * strength)
            else:
                a = int((t)**1.6 * strength)
            pygame.draw.line(surf, (0,0,0,a), (x,0), (x,rect.height))
        if side == "left":
            self.screen.blit(surf, rect.topleft)
        else:
            self.screen.blit(surf, (rect.right - band_w, rect.top))

    def draw_flip(self):
        # If no flipping, draw static pages
        if not (self.flip.flipping or self.flip.dragging):
            left = self.get_page_surface(self.index)
            right = self.get_page_surface(self.index + 1)

            self.draw_page(left, self.left_rect)
            self.draw_page(right, self.right_rect)

            # spine soft shading
            self.draw_shadow_band(self.left_rect, "right", 55)
            self.draw_shadow_band(self.right_rect, "left", 55)
            return

        # During flip: we simulate as a rotating/scaling "leaf"
        t = clamp(self.flip.t, 0.0, 1.0)
        et = ease_out_cubic(t)

        left_idx = self.index
        right_idx = self.index + 1

        if self.flip.direction == 1:
            # flipping RIGHT page to LEFT
            static_left = self.get_page_surface(left_idx)
            under_right = self.get_page_surface(right_idx + 1)  # next right page (after turn)
            flipping_front = self.get_page_surface(right_idx)   # current right
            flipping_back = self.get_page_surface(left_idx + 2) # new left after turn

            # draw static left
            self.draw_page(static_left, self.left_rect)

            # draw "under" right page (appears as next)
            self.draw_page(under_right, self.right_rect)

            # flip leaf over spine
            # leaf x position moves from right edge -> left edge
            start_x = self.right_rect.right
            end_x = self.left_rect.left
            leaf_x = lerp(start_x, end_x, et)

            # leaf width shrinks as it rotates (cosine)
            angle = et * math.pi
            scale_x = abs(math.cos(angle))
            leaf_w = max(8, int(self.page_w * scale_x))
            leaf_h = self.page_h

            # decide which face is visible
            show_front = angle < math.pi/2
            face = flipping_front if show_front else flipping_back

            # shear-ish highlight
            sheen = int(lerp(0, 85, smoothstep(1 - abs(0.5 - t)*2)))

            # transform
            face_scaled = pygame.transform.smoothscale(face, (leaf_w, leaf_h))
            leaf_rect = face_scaled.get_rect()
            leaf_rect.top = self.left_rect.top
            leaf_rect.centerx = int(leaf_x)

            # shadow intensity peaks mid-flip
            shadow_strength = int(lerp(30, SHADOW_MAX, smoothstep(1 - abs(t-0.5)*2)))

            # cast shadow on pages
            shadow = pygame.Surface((leaf_rect.width+60, leaf_rect.height), pygame.SRCALPHA)
            pygame.draw.ellipse(shadow, (0,0,0,shadow_strength), shadow.get_rect().inflate(0, -60))
            shadow = pygame.transform.smoothscale(shadow, shadow.get_size())
            self.screen.blit(shadow, (leaf_rect.x-30, leaf_rect.y))

            # leaf edge shadow
            self.draw_shadow_band(self.left_rect, "right", int(lerp(35, 85, smoothstep(t))))
            self.draw_shadow_band(self.right_rect, "left", int(lerp(85, 35, smoothstep(t))))

            # leaf itself
            self.screen.blit(face_scaled, leaf_rect.topleft)

            # leaf fold highlight (sheen)
            if sheen > 0:
                hl = pygame.Surface((leaf_rect.width, leaf_rect.height), pygame.SRCALPHA)
                for x in range(leaf_rect.width):
                    tt = x / max(1, leaf_rect.width-1)
                    a = int((1 - abs(tt-0.5)*2) ** 1.8 * sheen)
                    pygame.draw.line(hl, (255,255,255,a), (x,0), (x,leaf_rect.height))
                self.screen.blit(hl, leaf_rect.topleft)

        else:
            # flipping LEFT page to RIGHT (previous)
            static_right = self.get_page_surface(right_idx)
            under_left = self.get_page_surface(left_idx - 1)     # prev left page (after turn)
            flipping_front = self.get_page_surface(left_idx)     # current left
            flipping_back = self.get_page_surface(right_idx - 2) # new right after turn

            # draw under left page
            self.draw_page(under_left, self.left_rect)

            # draw static right
            self.draw_page(static_right, self.right_rect)

            start_x = self.left_rect.left
            end_x = self.right_rect.right
            leaf_x = lerp(start_x, end_x, et)

            angle = et * math.pi
            scale_x = abs(math.cos(angle))
            leaf_w = max(8, int(self.page_w * scale_x))
            leaf_h = self.page_h

            show_front = angle < math.pi/2
            face = flipping_front if show_front else flipping_back

            sheen = int(lerp(0, 85, smoothstep(1 - abs(0.5 - t)*2)))

            face_scaled = pygame.transform.smoothscale(face, (leaf_w, leaf_h))
            leaf_rect = face_scaled.get_rect()
            leaf_rect.top = self.left_rect.top
            leaf_rect.centerx = int(leaf_x)

            shadow_strength = int(lerp(30, SHADOW_MAX, smoothstep(1 - abs(t-0.5)*2)))

            shadow = pygame.Surface((leaf_rect.width+60, leaf_rect.height), pygame.SRCALPHA)
            pygame.draw.ellipse(shadow, (0,0,0,shadow_strength), shadow.get_rect().inflate(0, -60))
            self.screen.blit(shadow, (leaf_rect.x-30, leaf_rect.y))

         