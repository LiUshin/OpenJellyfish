import { createRoot } from 'react-dom/client';
import 'highlight.js/styles/github-dark.css';
import './styles/global.css';
import App from './App';

createRoot(document.getElementById('root')!).render(<App />);
