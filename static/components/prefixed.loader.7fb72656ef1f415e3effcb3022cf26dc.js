!function(){function e(e,o){return new Promise((function(t,n){document.head.appendChild(Object.assign(document.createElement("script"),{src:e,onload:t,onerror:n},o?{type:"module"}:void 0))}))}var o=[];function t(){e(static_url + "components/rp-components.2a79caef27c26e68230b.js")}"fetch"in window||o.push(e(static_url + "components/polyfills/fetch.e0fa1d30ce1c9b23c0898a2e34c3fe3b.js",!1)),"attachShadow"in Element.prototype&&"getRootNode"in Element.prototype&&(!window.ShadyDOM||!window.ShadyDOM.force)||o.push(e(static_url + "components/polyfills/webcomponents.dae9f79d9d6992b6582e204c3dd953d3.js",!1)),!("noModule"in HTMLScriptElement.prototype)&&"getRootNode"in Element.prototype&&o.push(e(static_url + "components/polyfills/custom-elements-es5-adapter.84b300ee818dce8b351c7cc7c100bcf7.js",!1)),o.length?Promise.all(o).then(t):t()}();