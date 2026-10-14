const { expo } = require('./app.json');
module.exports = () => {
  const projectId = process.env.EAS_PROJECT_ID;
  if (projectId && !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(projectId)) {
    throw new Error('EAS_PROJECT_ID deve ser o UUID do projeto Expo autorizado.');
  }
  return {
    ...expo,
    ...(process.env.EXPO_OWNER ? { owner: process.env.EXPO_OWNER } : {}),
    ...(process.env.EXPO_PROJECT_SLUG ? { slug: process.env.EXPO_PROJECT_SLUG } : {}),
    ios: { ...expo.ios, ...(process.env.APPLE_TEAM_ID ? { appleTeamId: process.env.APPLE_TEAM_ID } : {}) },
    extra: { ...expo.extra, ...(projectId ? { eas: { projectId } } : {}) },
  };
};
