# Empty-chat ocean

The procedural Canvas 2D renderer is adapted from OpenJellyfish's owned website experiment at `openjellyfishPage/src/components/lab/ascii/` (A version, including the twelve layered central oral arms). Geometry, tentacle simulation and palette are retained. The website experiment is an original implementation inspired by character-rendered sea creatures; no external source code, fonts, images or runtime packages are bundled here.

`AsciiJellyfishBackground` supplies the ocean backdrop and lazy-loads the renderer. Mount it inside a sized, positioned chat pane, with interactive foreground content above its z-index of zero:

```tsx
<AsciiJellyfishBackground theme={isDark ? 'dark' : 'light'} paused={oceanPaused} />
```

It is decorative (`aria-hidden`, no pointer events), respects the system's reduced-motion preference, pauses while the document is hidden, and destroys its observer, visibility listener and animation frame on unmount. A disabled or unavailable canvas leaves a static CSS ocean. Theme/playback changes update the existing renderer; ordinary composer updates do not restart it.

The renderer limits painting to 30 fps, device pixel ratio to 2 and its character grid to about 70,000 cells. It shows six creatures on a wide canvas and four below 700 px. The physical chains update between painted frames. The initial reduced-motion frame keeps the same composition without animation.
