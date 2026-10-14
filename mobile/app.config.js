const { expo } = require('./app.json');
module.exports = () => {
  for (const key of ['EAS_PROJECT_ID','EXPO_OWNER','EXPO_PROJECT_SLUG','APPLE_TEAM_ID']) {
    if (process.env[key] && /[\[\]]|SUBSTITUA_PELO/i.test(process.env[key])) {
      throw new Error(key+' contém um identificador de exemplo; informe o valor real.');
    }
  }
  const projectId = process.env.EAS_PROJECT_ID;
  if (process.env.APPLE_TEAM_ID && !/^[A-Z0-9]{10}$/.test(process.env.APPLE_TEAM_ID)) {
    throw new Error('APPLE_TEAM_ID deve ter 10 caracteres alfanuméricos.');
  }
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
