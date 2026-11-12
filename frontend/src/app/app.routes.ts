import { Routes } from '@angular/router';

export const routes: Routes = [
  { path: '', title: 'Videos · Clipper', loadComponent: () => import('./pages/videos').then((m) => m.VideosPage) },
  {
    path: 'videos/:id',
    title: 'Video · Clipper',
    loadComponent: () => import('./pages/video-detail').then((m) => m.VideoDetailPage),
  },
  {
    path: 'campaigns',
    title: 'Campaigns · Clipper',
    loadComponent: () => import('./pages/campaigns').then((m) => m.CampaignsPage),
  },
  {
    path: 'ideas',
    title: 'Ideas · Clipper',
    loadComponent: () => import('./pages/ideas').then((m) => m.IdeasPage),
  },
  {
    path: 'scripts',
    title: 'Scripts · Clipper',
    loadComponent: () => import('./pages/scripts').then((m) => m.ScriptsPage),
  },
  {
    path: 'scripts/:id',
    title: 'Script · Clipper',
    loadComponent: () => import('./pages/script').then((m) => m.ScriptPage),
  },
  {
    path: 'animations',
    title: 'Animations · Clipper',
    loadComponent: () => import('./pages/animations').then((m) => m.AnimationsPage),
  },
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
