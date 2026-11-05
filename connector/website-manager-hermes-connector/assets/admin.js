/**
 * Tools → Hermes Connector page helpers: copy button and REST reachability check.
 *
 * @package WebsiteManagerHermesConnector
 */
( function () {
	'use strict';

	var cfg = window.wmhcAdmin || {};
	var i18n = cfg.i18n || {};

	function setText( el, text ) {
		if ( el ) {
			el.textContent = text;
		}
	}

	function initCopy() {
		var button = document.getElementById( 'wmhc-copy' );
		var field = document.getElementById( 'wmhc-connection-string' );
		var status = document.getElementById( 'wmhc-copy-status' );
		if ( ! field ) {
			return;
		}
		field.addEventListener( 'focus', function () {
			field.select();
		} );
		if ( ! button ) {
			return;
		}
		button.addEventListener( 'click', function () {
			var value = field.value;
			function fallback() {
				field.focus();
				field.select();
				try {
					var ok = document.execCommand( 'copy' );
					setText( status, ok ? i18n.copied : i18n.copyFailed );
				} catch ( e ) {
					setText( status, i18n.copyFailed );
				}
			}
			if ( navigator.clipboard && window.isSecureContext ) {
				navigator.clipboard.writeText( value ).then(
					function () {
						setText( status, i18n.copied );
					},
					fallback
				);
			} else {
				fallback();
			}
		} );
	}

	function initRestCheck() {
		var el = document.getElementById( 'wmhc-rest-check' );
		if ( ! el || ! cfg.statusUrl || ! window.fetch ) {
			return;
		}
		setText( el, i18n.checking );
		window
			.fetch( cfg.statusUrl, {
				credentials: 'same-origin',
				headers: { 'X-WP-Nonce': cfg.nonce },
			} )
			.then( function ( response ) {
				setText( el, response.ok ? i18n.reachable : i18n.unreachable + ' (HTTP ' + response.status + ')' );
			} )
			.catch( function () {
				setText( el, i18n.unreachable );
			} );
	}

	if ( document.readyState === 'loading' ) {
		document.addEventListener( 'DOMContentLoaded', function () {
			initCopy();
			initRestCheck();
		} );
	} else {
		initCopy();
		initRestCheck();
	}
} )();
