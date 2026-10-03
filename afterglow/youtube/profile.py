"""
The one persistent web profile every YouTube page in afterglow uses (Studio, sign-in, the embed
player), so signing in once in Settings is enough.

Google refuses sign-in from embedded browsers ("This browser or app may not be secure") unless the
sign-in page sees an ordinary browser.  qutebrowser -- the same engine -- fixes it by presenting a
Firefox user agent to accounts.google.com only; afterglow does the same: a request interceptor sets
the User-Agent header for the sign-in hosts, and a document-creation script makes
``navigator.userAgent`` (and friends) agree on those hosts only.  Every other host -- Studio
included -- sees the engine's normal Chromium identity.  If Google still refuses, Settings offers
the fallback (upload in the system browser, register the link afterwards).
"""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtWebEngineCore import (QWebEngineProfile, QWebEngineScript, QWebEngineUrlRequestInterceptor)

from .. import config as config_module

PROFILE_NAME = "afterglow-youtube"
SIGNIN_HOSTS = ("accounts.google.com", "accounts.youtube.com")
FIREFOX_UA = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"

_SIGNIN_SCRIPT = """
(function () {
  var hosts = %(hosts)s;
  if (hosts.indexOf(location.hostname) < 0) return;
  var ua = %(ua)s;
  function def(obj, name, value) {
    try { Object.defineProperty(obj, name, {get: function () { return value; }, configurable: true}); } catch (e) {}
  }
  def(Navigator.prototype, 'userAgent', ua);
  def(Navigator.prototype, 'appVersion', '5.0 (X11)');
  def(Navigator.prototype, 'platform', 'Linux x86_64');
  def(Navigator.prototype, 'vendor', '');
  def(Navigator.prototype, 'oscpu', 'Linux x86_64');
  def(Navigator.prototype, 'productSub', '20100101');
  try { delete Navigator.prototype.userAgentData; } catch (e) {}
  def(Navigator.prototype, 'userAgentData', undefined);
  try { delete window.chrome; } catch (e) {}
})();
"""


def profile_dir() -> Path:
    return config_module.DATA_DIR / "youtube-profile"


class _SignInInterceptor(QWebEngineUrlRequestInterceptor):
    def interceptRequest(self, info) -> None:  # noqa: N802 (Qt name)
        host = info.requestUrl().host()
        if host in SIGNIN_HOSTS:
            info.setHttpHeader(b"User-Agent", FIREFOX_UA.encode())
            # Firefox sends no client hints; blank the Chromium ones on the sign-in hosts
            for h in (b"Sec-CH-UA", b"Sec-CH-UA-Mobile", b"Sec-CH-UA-Platform", b"Sec-CH-UA-Full-Version-List"):
                info.setHttpHeader(h, b"")


_profile: "QWebEngineProfile | None" = None
_interceptor: "_SignInInterceptor | None" = None


def profile() -> QWebEngineProfile:
    """The shared profile (created on first use; needs a QApplication)."""
    global _profile, _interceptor
    if _profile is not None:
        return _profile
    base = profile_dir()
    base.mkdir(parents=True, exist_ok=True)
    p = QWebEngineProfile(PROFILE_NAME)
    p.setPersistentStoragePath(str(base / "storage"))
    p.setCachePath(str(base / "cache"))
    p.setPersistentCookiesPolicy(QWebEngineProfile.ForcePersistentCookies)
    p.setHttpCacheType(QWebEngineProfile.DiskHttpCache)
    _interceptor = _SignInInterceptor()
    p.setUrlRequestInterceptor(_interceptor)
    import json
    script = QWebEngineScript()
    script.setName("afterglow-signin-ua")
    script.setSourceCode(_SIGNIN_SCRIPT % {"hosts": json.dumps(list(SIGNIN_HOSTS)), "ua": json.dumps(FIREFOX_UA)})
    script.setInjectionPoint(QWebEngineScript.DocumentCreation)
    script.setWorldId(QWebEngineScript.MainWorld)
    script.setRunsOnSubFrames(True)
    p.scripts().insert(script)
    _profile = p
    return p


def sign_out(done=None) -> None:
    """Forget the sign-in: every cookie and all site storage of the profile."""
    p = profile()
    p.cookieStore().deleteAllCookies()
    p.clearHttpCache()
    try:
        p.clearAllVisitedLinks()
    except AttributeError:
        pass
    if done:
        done()


def is_signin_url(url: "QUrl | str") -> bool:
    host = QUrl(url).host() if isinstance(url, str) else url.host()
    return host in SIGNIN_HOSTS
