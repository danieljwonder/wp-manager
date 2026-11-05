<?php
/**
 * Uninstall: removes the connector's options.
 *
 * Application Passwords created for Hermes are left in place on purpose: they
 * belong to the user and can be revoked under Users → Profile.
 *
 * @package WebsiteManagerHermesConnector
 */

if ( ! defined( 'WP_UNINSTALL_PLUGIN' ) ) {
	exit;
}

$wmhc_options = array( 'wmhc_settings', 'wmhc_log', 'wmhc_updates.lock' );

if ( is_multisite() ) {
	foreach ( $wmhc_options as $wmhc_option ) {
		delete_site_option( $wmhc_option );
	}
	$wmhc_sites = get_sites(
		array(
			'fields' => 'ids',
			'number' => 0,
		)
	);
	foreach ( $wmhc_sites as $wmhc_site_id ) {
		switch_to_blog( $wmhc_site_id );
		delete_transient( 'wmhc_update_lock' );
		foreach ( $wmhc_options as $wmhc_option ) {
			delete_option( $wmhc_option );
		}
		restore_current_blog();
	}
} else {
	delete_transient( 'wmhc_update_lock' );
	foreach ( $wmhc_options as $wmhc_option ) {
		delete_option( $wmhc_option );
	}
}
