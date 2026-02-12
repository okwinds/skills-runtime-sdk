# Skills Runtime Studio MVP - Frontend

Industrial Editorial style React frontend for the Skills Runtime Studio MVP.

## Quick Start

```bash
# Install dependencies
npm install

# Start development server
npm run dev
```

The app will be available at `http://localhost:5173`

## Tech Stack

- **Framework**: React 18 + TypeScript
- **Build Tool**: Vite 6
- **Styling**: CSS Variables + Custom Design System
- **Aesthetic**: Industrial Editorial (dark theme with amber accents)

## Project Structure

```
src/
├── components/
│   ├── layout/          # Layout components
│   │   └── FilmStripSidebar.tsx    # Session sidebar with film strip tags
│   ├── skills/          # Skills-related components
│   │   ├── SkillList.tsx           # Skills grid display
│   │   └── SkillCreateForm.tsx     # Create skill form
│   ├── run/             # Run/SSE components
│   │   └── SSETimeline.tsx         # SSE event timeline
│   └── ui/              # Reusable UI components
│       ├── Button.tsx
│       ├── Card.tsx
│       ├── Input.tsx
│       └── Tabs.tsx
├── lib/
│   └── api.ts           # API client with mock data
├── pages/
│   └── App.tsx          # Main application component
├── styles/
│   └── design-system.css # CSS variables and design tokens
└── types/
│   └── index.ts         # TypeScript type definitions
```

## Design System

### Color Palette (Industrial Editorial)

```css
/* Primary backgrounds */
--color-bg-primary: #0f0f0f;
--color-bg-secondary: #1a1a1a;
--color-bg-tertiary: #252525;

/* Accents */
--color-accent-primary: #e8b86d;     /* Amber */
--color-accent-secondary: #6b9dc7;  /* Steel blue */
--color-accent-success: #7eb8a2;   /* Sage green */
--color-accent-error: #c97b7b;     /* Muted red */
```

### Key Features

1. **Film Strip Sidebar**: Session list with perforated edge design resembling film strips
2. **Timeline with Ticks**: SSE event timeline with mechanical tick marks
3. **Amber Accent Glow**: Active states feature amber glow effects
4. **Monospace Typography**: Code and data displayed in JetBrains Mono

## Mock API

The `src/lib/api.ts` file provides a complete mock implementation of the API with:

- Session management (create, list)
- Skill management (list, create)
- Run streaming with SSE events
- Realistic delays to simulate network latency

## Development

### Available Scripts

- `npm run dev` - Start development server
- `npm run build` - Build for production
- `npm run preview` - Preview production build
- `npm run lint` - Run ESLint

### Environment Variables

Create a `.env` file for environment-specific configuration:

```env
VITE_API_BASE_URL=http://localhost:8000/api
```

## Building for Production

```bash
npm run build
```

The built files will be in the `dist/` directory.

## License

MIT
