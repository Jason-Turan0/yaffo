export const generateTimestampString = () =>
    new Date().toISOString().replace(/:/g, '-').replace(/\..+/, '')
