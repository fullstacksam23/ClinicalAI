import { route } from './js/router.js';
import { initGlobalHandlers } from './js/events.js';

initGlobalHandlers();
window.addEventListener('hashchange', route);
route();