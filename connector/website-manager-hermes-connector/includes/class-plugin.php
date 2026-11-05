<?php
/**
 * Plugin bootstrap: wires the REST routes, abilities and admin page.
 *
 * @package WebsiteManagerHermesConnector
 */

namespace WebsiteManagerHermesConnector;

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

/**
 * Main plugin class (singleton).
 */
final class Plugin {

	/**
	 * Singleton instance.
	 *
	 * @var Plugin|null
	 */
	private static $instance = null;

	/**
	 * Shared service used by REST routes, abilities and the admin page.
	 *
	 * @var Service
	 */
	private $service;

	/**
	 * Returns the singleton instance.
	 *
	 * @return Plugin
	 */
	public static function instance() {
		if ( null === self::$instance ) {
			self::$instance = new self();
		}
		return self::$instance;
	}

	/**
	 * Constructor.
	 */
	private function __construct() {
		$this->service = new Service( new Action_Log(), new Settings() );
	}

	/**
	 * Returns the shared service.
	 *
	 * @return Service
	 */
	public function service() {
		return $this->service;
	}

	/**
	 * Registers WordPress hooks.
	 *
	 * @return void
	 */
	public function init() {
		$rest = new Rest_Controller( $this->service );
		add_action( 'rest_api_init', array( $rest, 'register_routes' ) );

		$abilities = new Abilities( $this->service );
		add_action( 'wp_abilities_api_categories_init', array( $abilities, 'register_category' ) );
		add_action( 'wp_abilities_api_init', array( $abilities, 'register_abilities' ) );

		if ( is_admin() ) {
			$admin = new Admin_Page( $this->service );
			add_action( 'admin_menu', array( $admin, 'register_menu' ) );
			add_action( 'admin_init', array( $admin, 'register_settings' ) );
			add_action( 'admin_enqueue_scripts', array( $admin, 'enqueue_assets' ) );
			add_filter( 'plugin_action_links_' . plugin_basename( WMHC_FILE ), array( $admin, 'action_links' ) );
		}
	}
}
