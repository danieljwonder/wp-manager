<?php
/**
 * Abilities API registration (WordPress 6.9+).
 *
 * @package WebsiteManagerHermesConnector
 */

namespace WebsiteManagerHermesConnector;

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

/**
 * Registers the connector operations as abilities. The hooks only fire when
 * the Abilities API exists, so nothing happens on older WordPress versions.
 */
class Abilities {

	const CATEGORY = 'website-manager-hermes';

	/**
	 * Shared service.
	 *
	 * @var Service
	 */
	private $service;

	/**
	 * Constructor.
	 *
	 * @param Service $service Service.
	 */
	public function __construct( Service $service ) {
		$this->service = $service;
	}

	/**
	 * Registers the ability category.
	 *
	 * @return void
	 */
	public function register_category() {
		if ( ! function_exists( 'wp_register_ability_category' ) ) {
			return;
		}
		wp_register_ability_category(
			self::CATEGORY,
			array(
				'label'       => __( 'Website maintenance (Hermes)', 'website-manager-hermes-connector' ),
				'description' => __( 'Check and run WordPress updates for the Hermes website manager.', 'website-manager-hermes-connector' ),
			)
		);
	}

	/**
	 * Registers abilities.
	 *
	 * @return void
	 */
	public function register_abilities() {
		if ( ! function_exists( 'wp_register_ability' ) ) {
			return;
		}

		wp_register_ability(
			'website-manager-hermes/get-status',
			array(
				'label'               => __( 'Get site status', 'website-manager-hermes-connector' ),
				'description'         => __( 'Returns connector, WordPress and PHP versions, environment, active theme and pending update counts.', 'website-manager-hermes-connector' ),
				'category'            => self::CATEGORY,
				'output_schema'       => array( 'type' => 'object' ),
				'execute_callback'    => array( $this, 'execute_get_status' ),
				'permission_callback' => array( $this->service, 'current_user_can_view' ),
				'meta'                => array(
					'show_in_rest' => true,
					// Discoverable by AI clients (WP 7.1+ `public`; MCP Adapter `mcp.public`).
					// Execution is still gated by the permission callback.
					'public'       => true,
					'mcp'          => array( 'public' => true ),
					'annotations'  => array(
						'readonly'    => true,
						'destructive' => false,
						'idempotent'  => true,
					),
				),
			)
		);

		wp_register_ability(
			'website-manager-hermes/list-updates',
			array(
				'label'               => __( 'List available updates', 'website-manager-hermes-connector' ),
				'description'         => __( 'Lists available core, plugin, theme and translation updates. Set refresh to true to check WordPress.org first.', 'website-manager-hermes-connector' ),
				'category'            => self::CATEGORY,
				'input_schema'        => array(
					'type'                 => 'object',
					'properties'           => array(
						'refresh' => array(
							'type'        => 'boolean',
							'description' => __( 'Force a fresh update check.', 'website-manager-hermes-connector' ),
							'default'     => false,
						),
					),
					'additionalProperties' => false,
					'default'              => array( 'refresh' => false ),
				),
				'output_schema'       => array( 'type' => 'object' ),
				'execute_callback'    => array( $this, 'execute_list_updates' ),
				'permission_callback' => array( $this->service, 'current_user_can_view' ),
				'meta'                => array(
					'show_in_rest' => true,
					// Discoverable by AI clients (WP 7.1+ `public`; MCP Adapter `mcp.public`).
					// Execution is still gated by the permission callback.
					'public'       => true,
					'mcp'          => array( 'public' => true ),
					'annotations'  => array(
						'readonly'    => true,
						'destructive' => false,
						'idempotent'  => true,
					),
				),
			)
		);

		wp_register_ability(
			'website-manager-hermes/run-updates',
			array(
				'label'               => __( 'Run updates', 'website-manager-hermes-connector' ),
				'description'         => __( 'Updates plugins, themes, WordPress core or translations using the core upgraders. Items empty means all of that type. Take a backup first.', 'website-manager-hermes-connector' ),
				'category'            => self::CATEGORY,
				'input_schema'        => array(
					'type'                 => 'object',
					'properties'           => array(
						'type'  => array(
							'type' => 'string',
							'enum' => Service::UPDATE_TYPES,
						),
						'items' => array(
							'type'    => 'array',
							'items'   => array( 'type' => 'string' ),
							'default' => array(),
						),
					),
					'required'             => array( 'type' ),
					'additionalProperties' => false,
				),
				'output_schema'       => array( 'type' => 'object' ),
				'execute_callback'    => array( $this, 'execute_run_updates' ),
				'permission_callback' => array( $this, 'can_run_updates' ),
				'meta'                => array(
					'show_in_rest' => true,
					// Discoverable by AI clients (WP 7.1+ `public`; MCP Adapter `mcp.public`).
					// Execution is still gated by the permission callback.
					'public'       => true,
					'mcp'          => array( 'public' => true ),
					'annotations'  => array(
						'readonly'    => false,
						'destructive' => true,
						'idempotent'  => false,
					),
				),
			)
		);
	}

	/**
	 * Execute: get-status.
	 *
	 * @return array<string, mixed>
	 */
	public function execute_get_status() {
		return $this->service->get_status();
	}

	/**
	 * Execute: list-updates.
	 *
	 * @param mixed $input Input.
	 * @return array<string, mixed>
	 */
	public function execute_list_updates( $input = null ) {
		$refresh = is_array( $input ) && ! empty( $input['refresh'] ) && 'false' !== $input['refresh'];
		return $this->service->get_updates( $refresh );
	}

	/**
	 * Permission: run-updates (per-type capability).
	 *
	 * @param mixed $input Input.
	 * @return bool
	 */
	public function can_run_updates( $input = null ) {
		$type = is_array( $input ) && isset( $input['type'] ) ? (string) $input['type'] : '';
		return $this->service->current_user_can_update( $type );
	}

	/**
	 * Execute: run-updates.
	 *
	 * @param mixed $input Input.
	 * @return array<string, mixed>|\WP_Error
	 */
	public function execute_run_updates( $input = null ) {
		$input = is_array( $input ) ? $input : array();
		$items = isset( $input['items'] ) && is_array( $input['items'] ) ? $input['items'] : array();
		return $this->service->run_updates( isset( $input['type'] ) ? (string) $input['type'] : '', $items, 'ability' );
	}
}
