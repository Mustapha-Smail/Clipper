import { Routes } from '@angular/router';

export const routes: Routes = [
  { path: '', title: 'Videos · Clipper', loadComponent: () => import('./pages/videos').then((m) => m.VideosPage) },
  {
    path: 'videos/:id',
    title: 'Video · Clipper',
    loadComponent: () => import('./pages/video-detail').then((m) => m.VideoDetailPage),
  },
  {
    path: 'themes',
    title: 'Themes · Clipper',
    loadComponent: () => import('./pages/themes').then((m) => m.ThemesPage),
  },
  {
    path: 'themes/:id',
    title: 'Theme · Clipper',
    loadComponent: () => import('./pages/theme').then((m) => m.ThemePage),
  },
  {
    path: 'prompts',
    title: 'Prompts · Clipper',
    loadComponent: () => import('./pages/prompts').then((m) => m.PromptsPage),
  },
  {
    path: 'campaigns',
    title: 'Campaigns · Clipper',
    loadComponent: () => import('./pages/campaigns').then((m) => m.CampaignsPage),
  },
  { path: 'ideas', redirectTo: 'themes' },
  { path: 'scripts', redirectTo: 'themes' },
  {
    path: 'scripts/:id',
    title: 'Script · Clipper',
    loadComponent: () => import('./pages/script').then((m) => m.ScriptPage),
  },
  { path: 'animations', redirectTo: 'themes' },
  {
    path: 'storyboards/:id',
    title: 'Storyboard · Clipper',
    loadComponent: () => import('./pages/storyboard').then((m) => m.StoryboardPage),
  },
  {
    path: 'props',
    title: 'Library · Clipper',
    loadComponent: () => import('./pages/props').then((m) => m.PropsPage),
  },
  { path: 'clips', title: 'Review · Clipper', loadComponent: () => import('./pages/clips').then((m) => m.ClipsPage) },
  {
    path: 'activity',
    title: 'Activity · Clipper',
    loadComponent: () => import('./pages/activity').then((m) => m.ActivityPage),
  },
  {
    path: 'settings',
    title: 'Settings · Clipper',
    loadComponent: () => import('./pages/settings').then((m) => m.SettingsPage),
  },
  { path: '**', redirectTo: '' },
];
