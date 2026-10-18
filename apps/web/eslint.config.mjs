import next from "eslint-config-next";
export default [...(Array.isArray(next) ? next : [])];
