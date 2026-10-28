/** Applied before first paint and kept alive across client-side navigation. */
export const THEME_INIT = `(function(){
  var root=document.documentElement, media=window.matchMedia('(prefers-color-scheme: dark)');
  var preference='light';
  try { preference=localStorage.getItem('nerv-theme') || 'light'; } catch(e) {}
  function apply(value){
    if(value!=='dark' && value!=='system') value='light';
    root.dataset.themePreference=value;
    root.dataset.theme=value==='system' ? (media.matches ? 'dark' : 'light') : value;
  }
  apply(preference);
  media.addEventListener('change',function(){apply(root.dataset.themePreference);});
  window.addEventListener('storage',function(event){
    if(event.key==='nerv-theme' || event.key===null) apply(event.newValue);
  });
})();`;
